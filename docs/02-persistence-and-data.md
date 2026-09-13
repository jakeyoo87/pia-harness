# Persistence contract and data

## Ownership boundary

`pia-harness` defines conversation domain records and the synchronous `ConversationStore` Protocol. It
does not open a database connection, know a table name, construct a physical key, configure TTL, or
authorize a user.

The consuming application implements the Protocol and owns:

- database and physical schema;
- stable opaque `user_key` mapping;
- retention duration and physical expiry;
- lifecycle checks and atomic write authorization;
- account-wide conversation deletion;
- infrastructure, IAM, encryption, backup, and deployment.

Harness components call the store through the injected Protocol. Store calls are synchronous and the
Orchestrator moves durable calls to worker threads.

## Domain records

The Protocol exchanges typed records rather than database items.

| Record | Purpose | Important fields |
| --- | --- | --- |
| `ActiveSession` | current reset boundary | `user_key`, `session_id`, `created_at` |
| `CompletedTurn` | one delivered combined exchange | identity, messages, `created_at`, `expires_at` |
| `RollingSummary` | one current Summary per session | `through_turn_id`, tokens, model, update time |
| `MemoryDocument` | one long-term document per user | text, `last_reviewed_turn_id`, update time |
| `ConversationContext` | Summary plus eligible recent Turns | `summary`, ordered `turns` |

No physical `PK`, `SK`, prefix, attribute name, table, index, or database type is part of these records.
Applications may use DynamoDB, RDS, a local database, or another implementation as long as the contract
is preserved.

## Required store operations

The runtime uses exactly these nine operations:

```text
get_or_create_active_session
append_completed_turn
load_context
get_memory
replace_memory
load_unreviewed_turns
replace_summary
delete_turns_through
reset_active_session
```

Account deletion is not called by a Harness component. The application owns its deletion method and
invokes it from its member lifecycle.

## Required semantics

### Isolation and completeness

Every operation is scoped by `user_key`. A store must never return or mutate another user's records.
Turn loads must return every eligible record, including data spanning multiple database pages.

### Turn ordering and expiry

`load_context` and `load_unreviewed_turns` return Turns in strictly increasing `turn_id` order.

- `load_context` returns only Turns strictly above its Summary boundary.
- `load_unreviewed_turns` returns only Turns strictly above `after_turn_id`, or all eligible Turns when
  the boundary is `None`; it ignores the rolling Summary boundary.
- both exclude Turns with `expires_at <= now` even when physical TTL deletion is delayed.

Memory Reviewer and Compactor validate returned identity, order, boundary, Turn ID, and expiry before a
write or covered-Turn deletion. A violation raises `StoreContractError` and fails closed. Completeness
cannot be inferred from returned data, so application stores must run the shared contract suite.

### Active session and reset

`get_or_create_active_session` produces one winner for concurrent creation. `reset_active_session`
replaces only the expected active session and raises `SessionConflictError` when the pointer changed.
The application implementation also removes the replaced session's Summary. Old raw Turns can remain
until their application-defined expiry; long-term Memory remains.

### Completed Turn replay

`append_completed_turn` validates a sortable Turn ID matching `created_at` and returns the stored record.
An identical replay succeeds. The same Turn ID with different content raises `TurnConflictError`.
Applications may impose a maximum encoded size and use `TurnTooLargeError`.

### Memory and Summary CAS

`replace_memory` and `replace_summary` compare the caller's observed boundary with the current boundary.
The first write requires absence. A lost compare-and-set returns `False` and never overwrites the winner.

The Memory document is at most 4,000 Unicode characters. An empty document remains valid because its
boundary prevents retained raw Turns from recreating content after confirmed complete forgetting.

Compaction writes the replacement Summary before calling `delete_turns_through`. The application store
must delete only the requested user's requested session Turns at or below the winning boundary.

## Application lifecycle veto

An application store or delivery port raises `ConversationAbandoned` when product lifecycle policy no
longer permits the conversation, such as a missing or withdrawing user.

`ConversationAbandoned` is not a `SessionStoreError`. It means deliberate application veto, not storage
failure, CAS loss, or a retryable outage. The Orchestrator clears the batch, performs no automatic retry,
adds no failure notice, and returns `ABANDONED`.

The application decides how to make the lifecycle check atomic with persistence. For example, an
application using one DynamoDB table can combine its member-state ConditionCheck with a conversation
mutation in one transaction. It must distinguish member-condition cancellation from Memory/Summary CAS,
Turn replay/conflict, and session conflict.

Deletion operations that remove conversation data are application-owned and should remain repeatable.
They are not gated by an ACTIVE check because deletion moves in the same direction as withdrawal.

## Reusable contract tests

`pia_harness.testing.ConversationStoreContract` is a `unittest` mixin for application adapters. It
checks:

- user isolation and increasing Turn order;
- identical replay and conflicting replay;
- expiry filtering at the exact boundary;
- strict Summary and Memory boundaries;
- CAS winner preservation;
- reset conflict;
- complete loads beyond 1 MiB so pagination cannot silently truncate context.

Harness runs the same suite against `InMemoryConversationStore`, a test-only reference implementation.
Consuming applications subclass the mixin and return their real store from `make_store`. Application-
specific membership transactions, physical keys, pagination implementation, and account deletion need
additional application tests.

`InMemoryConversationStore` and the contract mixin live in `pia_harness.testing`; they are not exported
from the package root and are not production storage adapters.
