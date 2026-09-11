# pia-harness current documentation

These documents describe the current merged library. They are organized by the questions a new Agent
usually needs to answer before changing code.

## Reading order

1. Read the repository `AGENTS.md` and root `README.md`.
2. Read [Architecture](01-architecture.md) for component ownership and dependencies.
3. Choose the document that matches the change:
   - DynamoDB keys, TTL, CAS, reset, deletion: [Persistence and data](02-persistence-and-data.md)
   - request flow, interruption, Memory, Compaction: [Conversation lifecycle](03-conversation-lifecycle.md)
   - OpenRouter, retry, smoke tests, `pia` wiring: [Model Adapter and integration](04-model-adapter-and-integration.md)
4. Read the linked source and tests before editing.
5. Use `plans/` only when historical decisions or independent review records are needed.

## Document map

| Document | Primary source | Primary tests |
| --- | --- | --- |
| [01 Architecture](01-architecture.md) | `src/pia_harness/__init__.py`, all modules | all tests |
| [02 Persistence and data](02-persistence-and-data.md) | `session.py`, `dynamodb.py` | `test_conversation_store.py`, `test_memory.py`, `test_compaction.py` |
| [03 Conversation lifecycle](03-conversation-lifecycle.md) | `context.py`, `memory.py`, `compaction.py`, `orchestrator.py` | `test_context.py`, `test_memory.py`, `test_compaction.py`, `test_orchestrator.py` |
| [04 Model Adapter and integration](04-model-adapter-and-integration.md) | `budget.py`, `openrouter.py`, `scripts/smoke_openrouter_model.py` | `test_openrouter_model.py`, `test_openrouter_model_smoke.py` |

## Documentation policy

- `docs/` states what the current code does.
- `plans/` records why a feature was designed that way and includes historical reviews.
- `README.md` is the short entry point and runnable quick start.
- Source and tests are authoritative.
- Update the matching current-state document whenever its contract changes.
- Do not copy live credentials, user data, provider response bodies, or deployment records into this
  repository.
