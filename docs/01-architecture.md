# Architecture

## Purpose and boundary

`pia-harness` is the reusable conversation runtime below the PIA application. It owns deterministic
conversation state, long-term Memory, context construction, compaction, orchestration, and OpenRouter
request/response adaptation.

It does not own users, channels, product configuration, database access, AWS provisioning, or deployment.

```text
pia
├── authenticated member and opaque user_key
├── Telegram/channel delivery
├── system prompt and localized failure messages
├── exact model, context, timeout and retry choices
├── ConversationStore implementation and physical schema
├── Secrets Manager and infrastructure/IAM configuration
└── lifecycle and deployment
        │
        ▼
pia-harness
├── ConversationStore protocol
├── PromptContextAssembler
├── AutomaticMemoryReviewer
├── TokenCompactor
├── OpenRouterModelAdapter
└── ConversationOrchestrator
```

The application supplies dependencies explicitly. The library has no global container, service locator,
AWS resource discovery, environment parser, or channel abstraction.

## Component graph

```text
Application store ─implements─> ConversationStore protocol
                                      ▲
                                      │ reads / writes
                         ConversationOrchestrator
                           ├─ PromptContextAssembler ← token counter
                           ├─ AutomaticMemoryReviewer ← review callable
                           ├─ TokenCompactor ← summarize callable
                           └─ generate + application delivery callables
                                      ▲
                                  user input

OpenRouterModelAdapter may supply the four model callables, but model and storage adapters are
independent choices.
```

The Orchestrator owns sequencing, not model or storage policy. Each lower component keeps a narrow
contract and can be tested with fakes.

## Module map

| Module | Responsibility |
| --- | --- |
| `budget.py` | Immutable `ModelTokenBudget` shared by Assembler, Compactor, Adapter, and Orchestrator |
| `session.py` | Domain records, UTC helpers, sortable unique Turn IDs, Memory character constant |
| `persistence.py` | Application-owned store Protocol, DB-independent errors, abandon signal, loaded-Turn validation |
| `testing.py` | Reusable store contract tests and a test-only in-memory reference store |
| `context.py` | Ordered typed Context parts, trust labels, identity/boundary validation, input-budget check |
| `memory.py` | Review scheduling, explicit current-input Review, output validation, Memory CAS |
| `compaction.py` | Trigger policy, protected tail, rolling Summary replacement, covered-Turn deletion |
| `orchestrator.py` | Per-user generation ownership, interruption, commit serialization, delivery and failure statuses |
| `openrouter.py` | Model-agnostic OpenRouter rendering, structured output, safe errors, token usage and bounded retry |
| `scripts/smoke_openrouter_model.py` | Approved live compatibility measurement using fixed synthetic inputs |

The package root exports the intended domain and component API. Internal helpers and Orchestrator state
are not integration contracts.

## Core dependency rules

- One opaque `user_key` is the ownership boundary everywhere. The library never derives it from a
  Telegram ID, email, Cognito subject, or account number.
- Conversation components depend only on `ConversationStore`. The application owns DB access, physical
  keys, retention configuration, lifecycle authorization, account deletion, and atomicity.
- One `ModelTokenBudget` instance should be passed to the Adapter and Orchestrator. Assembler and
  Compactor derive their limits from it; do not reconstruct equivalent numbers separately.
- The Adapter produces `GeneratedAnswer`, `MemoryReviewOutput`, and `SummaryOutput`; it does not persist
  or deliver them.
- The Memory Reviewer and Compactor own domain validation and request conditional persistence after
  provider output is parsed. The injected store implements that persistence contract.
- The Orchestrator is the only component that combines concurrency, Memory, Compaction, delivery, and
  completed-Turn persistence.
- The consuming application owns all user-visible channel behavior and runtime configuration values.

## Trust boundary

Only `PromptContextKind.SYSTEM` with `PromptTrust.TRUSTED_INSTRUCTION` becomes model system content.

The following always remain untrusted data:

- long-term Memory;
- rolling Conversation Summary;
- historical user and assistant Turns;
- current user input.

The Adapter preserves historical user/assistant roles but labels Memory and Summary as data in user
messages. It rejects a non-system Context part marked trusted or a misplaced system part. Memory and
conversation content cannot change system policy, tool authority, Risk Check, or order approval.

## Process and concurrency boundary

The current Orchestrator coordinates concurrent messages within one process:

- per-user generation and commit state is isolated;
- different users can generate and commit independently;
- a newer message can supersede generation but not an owned commit;
- generation IDs suppress late provider results even if cancellation is ignored;
- durable work for one user is serialized by one commit lock.

There is no distributed lock, durable in-flight state, queue, worker, outbox, or cross-process generation
coordination. A future multi-process deployment must add an application-level coordination design rather
than assuming the in-process locks extend across workers.

## Change routing

- Persistence shape or lifecycle: update `02-persistence-and-data.md`.
- Prompt ordering, Memory schedule, Compaction, or Orchestrator sequencing: update
  `03-conversation-lifecycle.md`.
- OpenRouter payloads, retry, model settings, smoke behavior, or `pia` construction: update
  `04-model-adapter-and-integration.md`.
- Major responsibility changes: update this document and the root README.

Historical feature decisions and independent reviews remain under `plans/`; do not append new current
behavior only there.
