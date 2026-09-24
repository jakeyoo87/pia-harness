# pia-harness

Reusable conversation, long-term Memory, context, orchestration, persistence contracts, and OpenRouter
model components for PIA. The consuming application supplies user identity, storage implementation,
configuration, secrets, channel delivery, and deployment.

## Current capabilities

| Area | Current behavior |
| --- | --- |
| Sessions and Turns | One active session per opaque user key and completed Turn lifecycle through an application store |
| Context | User-isolated Memory, rolling Summary, recent Turns, and current input assembled under one token budget |
| Compaction | 90% trigger, protected recent tail, latest Turn preservation, one rolling Summary per session |
| Long-term Memory | One user document up to 4,000 characters, one-hour revisit Review, explicit update/forget, CAS writes |
| Orchestration | Hermes-style interruption during generation, serialized commit, bounded overflow recovery |
| Model access | Model-agnostic OpenRouter Adapter for structured Answer/Memory/Summary, bounded retry, and optional model-decided two-stage Web Search |
| Host tools | Optional single structured tool request per Answer, executed after commit ownership through an application callback; host-controlled delivery and stored text |
| Diagnostics | Eight-scenario base smoke plus two optional Web Search scenarios |

The current implementation is single-process. Distributed coordination, Telegram, AWS infrastructure,
and deployment belong to the consuming application.

## Documentation

Start with [the documentation index](docs/README.md).

- [Architecture](docs/01-architecture.md)
- [Persistence and data](docs/02-persistence-and-data.md)
- [Conversation lifecycle](docs/03-conversation-lifecycle.md)
- [Model Adapter and integration](docs/04-model-adapter-and-integration.md)

`docs/` describes the current merged implementation. `plans/` preserves design decisions, review
records, and historical constraints. Source and tests are authoritative if documentation drifts.

## Requirements and installation

- Python 3.12 or newer
- OpenRouter API key only when using `OpenRouterModelAdapter`

Install from a checkout:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Tagged versions such as `v0.3.0` also publish a wheel on GitHub Releases. Consuming applications should
pin that wheel with its SHA-256.

The only runtime dependency is bounded in `pyproject.toml`: httpx for the optional OpenRouter Adapter.

## Minimal construction

The caller implements `ConversationStore`, creates one shared budget, and passes its store plus the
Adapter's callables into the existing components:

```python
from pia_harness import OpenRouterWebSearchConfig

store = ApplicationConversationStore(...)
budget = ModelTokenBudget(context_limit=1_050_000, response_tokens=4_096)
adapter = OpenRouterModelAdapter(
    api_key=openrouter_key,
    model_id="vendor/exact-model-id",
    token_budget=budget,
    timeout_seconds=15,
    max_attempts=2,
    # Optional; policy and values belong to the consuming application.
    # web_search=OpenRouterWebSearchConfig("exa", 3, 5, "low"),
)

assembler = PromptContextAssembler(adapter.count_input_tokens)
memory_reviewer = AutomaticMemoryReviewer(store, adapter.review_memory)
compactor = TokenCompactor(store, adapter.summarize)
```

The consuming application then creates `ConversationOrchestrator` with those three components, the same
`store` and `budget`, `adapter.generate_answer`, `adapter.model_id`, a channel-specific async `deliver`
callable, its system prompt, and a required localized explicit-Memory failure notice. See the
[integration guide](docs/04-model-adapter-and-integration.md) for the complete example and ownership
boundary.

Always call `await adapter.aclose()` when shutting down an Adapter that owns its HTTP clients.

## Verification

Run the full DB- and network-free suite:

```bash
python -m unittest discover -s tests -v
```

Run only model and Orchestrator tests:

```bash
python -m unittest tests.test_openrouter_model tests.test_openrouter_model_smoke \
  tests.test_orchestrator -v
```

Run the live smoke tool only with approval and an exact model ID. The API key must already be present in
the process environment and must never be passed as an argument or committed:

```bash
PYTHONPATH=src python scripts/smoke_openrouter_model.py \
  --model vendor/exact-model-id \
  --context-limit 1050000 \
  --response-tokens 4096 \
  --timeout-seconds 15
```

Add `--web-search-engine exa --web-search-context-size low` to run the two additional synthetic
search/no-search scenarios. A structured Answer first decides whether search is needed; only a positive
decision starts a second cited-text call with the tool. Memory Review and Summary never receive it.
Optional `--web-search-max-results` and `--web-search-max-total-results` flags override the provider
defaults only when explicitly supplied.

The tool performs eight fixed base calls and, when Web Search is configured, two additional Answer calls.
It never retries internally, emits JSON lines, and exits `0` only when every scenario passes with one
observed response model. Exit `1` is a model/Adapter mismatch; exit `2` is invalid local configuration.

## Non-goals

- No channel, Telegram, member authentication, portfolio, Risk Check, or built-in order execution; applications own tool implementations and authorization
- No database client or adapter, physical key schema, AWS table/IAM provisioning, or Secrets Manager lookup
- No model selection, fallback hierarchy, dynamic Models API discovery, or reasoning policy
- No queue, worker, outbox, distributed lock, Memory history, vector search, or administration UI
- No live calls in automated tests
