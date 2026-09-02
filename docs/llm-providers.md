# LLM provider interface

TheAppliedScientist names the wire protocol explicitly. A model name and an HTTP
protocol are separate choices.

## Claude Code: Anthropic format only

Both AI Reviewer and the default AI Scientist agent are Claude Code processes.
They require an endpoint implementing Anthropic's Messages API semantics,
including streaming and tool-use events.

Required variables:

```dotenv
ANTHROPIC_BASE_URL=https://provider.example/anthropic
ANTHROPIC_AUTH_TOKEN=...
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=provider-model-name
REVIEW_MODEL=provider-reviewer-model-name
```

The shared values are defaults. Different machines or providers can be selected
without changing code:

```dotenv
SCIENTIST_ANTHROPIC_BASE_URL=https://provider.example/anthropic
SCIENTIST_ANTHROPIC_API_KEY=...
SCIENTIST_ANTHROPIC_MODEL=claude-opus-4-8[1m]

REVIEW_ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
REVIEW_ANTHROPIC_API_KEY=...
REVIEW_MODEL=deepseek-v4-flash
```

These are not different provider types. Both URLs implement the same Anthropic
Messages interface consumed by Claude Code.

`ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` are both populated by the setup
wizard because supported gateways and pinned Harbor/Claude Code versions do not
all read the same name.

### DeepSeek

```dotenv
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-pro[1m]
REVIEW_MODEL=deepseek-v4-flash
```

DeepSeek currently publishes a native Anthropic endpoint, so no converter is
needed.

### Direct Anthropic

```dotenv
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_MODEL=claude-opus-4-6
REVIEW_MODEL=claude-sonnet-4-6
```

### OpenAI-only or OpenAI-compatible provider

Claude Code cannot consume `/chat/completions` directly. Run a converter that
accepts Anthropic Messages requests and translates them to the provider's OpenAI
API, then configure the converter URL as `ANTHROPIC_BASE_URL`. The converter must
translate streaming events, tool calls, thinking blocks, and stop reasons, not
only rename JSON fields.

Components that genuinely speak OpenAI format use the separate
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL` variables (or their
component-specific equivalents). Those values are never forwarded to Claude Code.

## Search

`GEMINI_API_KEY` is required for vector queries because the published vectors
were generated with Gemini Embedding 2. The model and its 3072-dimensional vector
space are part of the index format. Supporting another embedding model requires
building and publishing a corresponding full index.

The context-distillation `/query_paper` endpoint is independent:

```dotenv
SEARCH_QUERY_API_FORMAT=gemini   # or anthropic, openai
SEARCH_QUERY_BASE_URL=
SEARCH_QUERY_API_KEY=
SEARCH_QUERY_MODEL=gemini-3-flash-preview
```

The `gemini` path is the reference default. Anthropic and OpenAI paths
use their respective native message formats.

## Other scientist agents

The Scientist retains optional `--agent codex` and `--agent gemini-cli` modes.
Those CLIs use their native provider configuration. They are useful extensions,
but the reference experiment setup uses Claude Code and an Anthropic-format endpoint.
