#!/bin/bash
# Self-healing wrapper for the `claude` CLI. Installed in place of the real
# binary (which moves to /opt/claude-real/claude) so harbor's invocation is
# unchanged: it still just runs `claude --print -- <instruction>`.
#
# Internally: runs the real binary, tails its output to our own stdout (so
# harbor's `| tee /logs/agent/claude-code.txt` still sees everything), and if
# output goes silent too long (a hung API call to the proxy), kills the stuck
# process and relaunches with --continue — which resumes the exact chat
# session from its on-disk JSONL checkpoint, same as closing/reopening
# Claude Code locally. From harbor's point of view this is one unbroken
# foreground command until the run genuinely finishes.
set -uo pipefail

REAL_CLAUDE="/opt/claude-real/claude"
STALL_SECS="${CLAUDE_SELFHEAL_STALL_SECS:-300}"
POLL_SECS="${CLAUDE_SELFHEAL_POLL_SECS:-10}"
HEAL_LOG="/tmp/claude-selfheal.log"
log() { echo "[selfheal $(date -u +%FT%TZ)] $*" >> "$HEAL_LOG"; }

ORIG_ARGS=("$@")

IS_PRINT=0
for a in "${ORIG_ARGS[@]}"; do
  [[ "$a" == "--print" || "$a" == "-p" ]] && IS_PRINT=1
done
if [[ "$IS_PRINT" -ne 1 ]]; then
  exec "$REAL_CLAUDE" "${ORIG_ARGS[@]}"
fi

BASE_FLAGS=()
i=0
n=${#ORIG_ARGS[@]}
while (( i < n )); do
  if [[ "${ORIG_ARGS[$i]}" == "--" ]]; then
    i=$((i+1))
    break
  fi
  BASE_FLAGS+=("${ORIG_ARGS[$i]}")
  i=$((i+1))
done

attempt=0
while true; do
  attempt=$((attempt+1))
  TMP_OUT=$(mktemp)

  if (( attempt == 1 )); then
    "$REAL_CLAUDE" "${ORIG_ARGS[@]}" > "$TMP_OUT" 2>&1 < /dev/null &
  else
    log "attempt $attempt: relaunching with --continue"
    "$REAL_CLAUDE" "${BASE_FLAGS[@]}" --continue --print -- \
      "Continue the research task from exactly where you left off. Do not restart or repeat already-completed work." \
      > "$TMP_OUT" 2>&1 < /dev/null &
  fi
  CLAUDE_PID=$!

  tail -n +1 -f "$TMP_OUT" &
  TAIL_PID=$!

  while kill -0 "$CLAUDE_PID" 2>/dev/null; do
    sleep "$POLL_SECS"
    NOW=$(date +%s)
    LAST_MOD=$(stat -c %Y "$TMP_OUT" 2>/dev/null || echo "$NOW")
    AGE=$((NOW - LAST_MOD))
    if (( AGE > STALL_SECS )); then
      log "attempt $attempt: stall detected (${AGE}s idle), killing pid $CLAUDE_PID"
      kill -9 "$CLAUDE_PID" 2>/dev/null
      break
    fi
  done

  wait "$CLAUDE_PID" 2>/dev/null
  EXIT_CODE=$?
  sleep 0.5
  kill "$TAIL_PID" 2>/dev/null
  wait "$TAIL_PID" 2>/dev/null
  rm -f "$TMP_OUT"

  if (( EXIT_CODE == 0 )); then
    log "attempt $attempt: claude exited cleanly"
    exit 0
  fi
  log "attempt $attempt: claude exited $EXIT_CODE, retrying"
  sleep 3
done
