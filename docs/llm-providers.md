# API keys, models, and endpoints

[Back to setup](../README.md#2-prepare-api-keys)

Interactive `./tas setup` asks for the selected components' keys and agent endpoints, then saves them in the root `.env`. Search's answer-model settings are edited in that file separately. Restart the affected API after changing its configuration.

## Which keys do I need?

| Component | Credentials | Model |
| --- | --- | --- |
| Search embeddings | `GEMINI_API_KEY` from [Google AI Studio](https://ai.google.dev/gemini-api/docs/api-key) | Gemini Embedding 2, matching the published index |
| Search full-paper answers | Same Gemini key by default, or `SEARCH_QUERY_API_KEY` for another provider | `SEARCH_QUERY_MODEL` |
| AI Reviewer | `REVIEW_ANTHROPIC_API_KEY` | `REVIEW_MODEL` |
| AI Scientist | `SCIENTIST_ANTHROPIC_API_KEY` | `SCIENTIST_ANTHROPIC_MODEL` |

Hosted MCP users do not supply these keys. These settings are for running your own components.

## Reviewer and Scientist

Both agents use Claude Code. Their endpoint must implement the **Anthropic Messages API**, including streaming and tool calls. The model can come from any provider that implements this interface.

To use different providers for the two agents, set:

```dotenv
REVIEW_ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
REVIEW_ANTHROPIC_API_KEY=your-reviewer-key
REVIEW_MODEL=deepseek-v4-flash

SCIENTIST_ANTHROPIC_BASE_URL=https://your-provider.example/anthropic
SCIENTIST_ANTHROPIC_API_KEY=your-scientist-key
SCIENTIST_ANTHROPIC_MODEL=your-scientist-model
```

Replace the Scientist URL and model with those supported by your provider. These are configuration examples, not a change to the reference experiment models. [Original experiment setup](reproducing-experiments.md).

### Shared settings and overrides

Component settings above take priority. If left blank, credentials and endpoint fall back to the shared values:

```dotenv
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_API_KEY=your-key
ANTHROPIC_AUTH_TOKEN=your-key
ANTHROPIC_MODEL=your-scientist-model
```

Set both shared key names to the same key when editing manually. The launch code maps the selected component's key to both names for Claude Code. `REVIEW_MODEL` selects the Reviewer model; `SCIENTIST_ANTHROPIC_MODEL` takes priority over `ANTHROPIC_MODEL` for the Scientist.

The `ANTHROPIC_DEFAULT_*_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL` settings control Claude Code's model aliases and subagents. If changing Reviewer providers, check those model names are available from that provider too. A component-specific Scientist model clears these shared aliases for its launch.

### Direct Anthropic or a native Anthropic-compatible provider

Use `https://api.anthropic.com` for direct Anthropic. DeepSeek's native Anthropic endpoint and other compatible endpoints use the same settings; no special adapter is needed when they already support Messages format.

### OpenAI-compatible provider

Search's answer model can call an OpenAI-compatible API directly. Claude Code cannot. For Reviewer or Scientist, provide an **OpenAI-to-Anthropic converter** and set the agent's base URL to the converter's Anthropic interface.

The converter must handle streaming, tool calls, thinking blocks, and stop reasons. Setting `OPENAI_BASE_URL` alone does not configure either Claude Code agent.

## Search

### Embeddings

Set:

```dotenv
GEMINI_API_KEY=your-google-ai-key
```

The published index uses **Gemini Embedding 2** with 3072-dimensional vectors. Keep this embedding model when using the published index. Changing the answer model does not change embeddings; a different embedding model needs a matching rebuilt index.

### Full-paper answers: default Gemini

The `/query_paper` endpoint reads paper source and answers a question. It has its own model settings:

```dotenv
SEARCH_QUERY_API_FORMAT=gemini
SEARCH_QUERY_MODEL=gemini-3-flash-preview
SEARCH_QUERY_API_KEY=
SEARCH_QUERY_BASE_URL=
```

With a blank `SEARCH_QUERY_API_KEY`, this uses `GEMINI_API_KEY`. The default Gemini path uses Google's API directly; `SEARCH_QUERY_BASE_URL` is used by the Anthropic and OpenAI paths.

### Full-paper answers: OpenAI-compatible endpoint

Keep `GEMINI_API_KEY` for embeddings, then set:

```dotenv
SEARCH_QUERY_API_FORMAT=openai
SEARCH_QUERY_BASE_URL=https://your-provider.example/v1
SEARCH_QUERY_API_KEY=your-answer-model-key
SEARCH_QUERY_MODEL=your-answer-model
```

The client appends `/chat/completions`, so provide the API base URL rather than that route. If the base URL is blank, it uses `https://api.openai.com/v1`. A blank `SEARCH_QUERY_API_KEY` falls back to `OPENAI_API_KEY`.

### Full-paper answers: Anthropic-compatible endpoint

```dotenv
SEARCH_QUERY_API_FORMAT=anthropic
SEARCH_QUERY_BASE_URL=https://your-provider.example/anthropic
SEARCH_QUERY_API_KEY=your-answer-model-key
SEARCH_QUERY_MODEL=your-answer-model
```

The client appends `/v1/messages`. A blank base URL uses `https://api.anthropic.com`; a blank key falls back to the shared Anthropic key. Use `SEARCH_QUERY_API_KEY` to keep Search credentials separate from the agents.

Set these values **after setup and before `./tas up`**. If Search is already running, restart it to load them. Prefer a dedicated key for each component when deploying on separate machines.

## Other Scientist agents

Optional `--agent codex` and `--agent gemini-cli` modes use those CLIs' native provider settings. The reference experiment path uses Claude Code and an Anthropic-compatible endpoint. See [experiment compatibility](reproducing-experiments.md) before comparing runs.
