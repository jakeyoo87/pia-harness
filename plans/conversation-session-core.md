# Conversation Session Core Plan

## Goal

Build only the first usable pia-harness capability: persist and restore each authenticated user's completed conversation turns in DynamoDB.

This branch remains plan-only until independent review is complete. Implementation will continue on the same branch after review; the plan is not merged separately.

## Product decisions

- A turn is one accepted user message plus the final assistant response successfully delivered to that user.
- Store completed turns only.
- Do not store streaming tokens, hidden reasoning, typing indicators, tool traces, authentication data, operational commands, or undelivered responses.
- Raw turns have a configurable maximum retention of 14 days.
- Long-term memories explicitly requested by the user will be stored separately in a later feature and retained until deletion or account removal.
- S3 is not used for conversation turns or long-term memory in this feature.
- Future compaction will be driven by the complete assembled-context token count, not a turn-count threshold.
- When a model/provider reports token usage, future compaction will prefer that value. Pre-request checks may use a model tokenizer or a conservative estimate.
- Token-based compaction and explicit long-term memory are required follow-up capabilities. They are
  excluded only so this branch can deliver and verify one feature at a time.

## Scope

1. Minimal Python package structure.
2. Conversation session and completed-turn domain types.
3. Create or retrieve the active session for one authenticated user.
4. Append one completed turn.
5. Read recent, unexpired turns in chronological order.
6. Reset by replacing the active session pointer with a new session ID.
7. Delete all session and turn data for one user after the caller has blocked new writes for that user.
8. DynamoDB-backed repository.
9. Minimal dedicated DynamoDB table definition with partition key, sort key, and TTL only.
10. Configuration for table name and raw-turn retention days.
11. Focused DynamoDB Local tests for isolation, ordering, reset, deletion, and expiry filtering.

## Non-goals

- Nemotron or any other model call.
- Context assembly or system prompts.
- Compaction or summarization.
- Long-term memory implementation.
- Automatic memory extraction or learning.
- Skills, hooks, plugins, vector search, or embeddings.
- S3 storage.
- Telegram or PIA Bot integration.
- Production AWS deployment.
- GSI, secondary indexes, analytics, or archival.

## Trust and user isolation

PIA performs authentication. The harness accepts only a trusted, opaque user key derived by the caller from its authenticated member identity.

Every repository operation requires a user key. There is no interface that loads a session or turn by session ID alone.

Partition boundary:

- PK: opaque user key
- SK for the active session pointer: a single fixed value per user, holding the current session ID
- SK for turns: session, session ID, and a lexically sortable turn ID

The dedicated table name separates environments. A namespace parameter is intentionally omitted;
no current consumer needs several logical applications inside one table.

The library must not require an email address, Telegram ID, Cognito subject, or other direct identifier. It must not log message content or user keys.

## Data model

### Active session pointer

- user_key
- session_id
- created_at

Exactly one pointer exists per user. There is no CLOSED session row: no access pattern reads one,
and unbounded session rows would make every get-or-create read a growing history. The pointer
carries no TTL, and updated_at is not written per turn because nothing reads it.

### Completed turn

- user_key
- session_id
- turn_id
- user_message
- assistant_message
- created_at
- expires_at

Turn identifiers are generated once when the caller accepts a user message and are unique within a
user's session. They contain a fixed-width UTC timestamp followed by random entropy, so their lexical
order is chronological and the same ID addresses the same item on every retry. created_at is supplied
with the turn as a regular attribute and expires_at is derived from it and the retention setting.

Timestamps are stored in UTC with a fixed-width format, so lexical sort order equals chronological
order. expires_at is stored as epoch seconds in a Number attribute, which is the only form the
DynamoDB TTL service acts on.

## Access patterns

The first feature supports only:

1. Get or create the active session for a user: one GetItem on the pointer, then a conditional
   create when it is absent.
2. Append a completed turn to that user's active session: one conditional Put.
3. Query recent unexpired turns for that user's session in chronological order: one Query on the
   turn key prefix.
4. Reset: one conditional write that moves the pointer to a new session ID.
5. Delete all data for one user: query only that user's partition through all pages and delete every
   returned item. The caller must block new writes before invoking deletion.

The design must not require Scan operations, and none of the five needs a secondary index.

## Retention

Each completed turn receives an expires_at value calculated from the configurable retention period, defaulting to 14 days.

DynamoDB TTL performs physical deletion asynchronously. Reads must therefore exclude expired turns even when DynamoDB has not removed them yet.

The first API returns all unexpired turns in the active session and follows DynamoDB pagination. It
does not add a turn-count limit; future context selection will use a token budget.

The pointer must never carry a TTL. Turns expire while the pointer stays, which is intended: a user
returning after the retention window keeps the same session with no turns.

Reset moves the pointer to a new session ID. It does not define or delete future long-term memories,
and it does not delete the previous session's turns, which remain until they expire.

Account closure calls delete_all_for_user and removes the pointer and every remaining turn without
waiting for TTL. The operation is idempotent: retrying after a partial failure deletes whatever
remains. It does not add a worker, outbox, deletion state machine, or recovery ledger.

## Write boundary

The caller appends a turn only after the assistant response has been successfully delivered. The harness does not decide whether delivery succeeded.

An append retry for the same user, session and turn ID must not create a duplicate turn. Conflicting
reuse of a turn ID must fail rather than overwrite different content. Both follow
from a conditional Put that requires the item to be absent, followed by a content comparison when
the condition fails.

A turn larger than the configured maximum is rejected before the write. A DynamoDB item is capped at
400 KB, and without an explicit bound the first long answer would fail at write time after the user
had already received it.

## Future compaction compatibility

No compaction code is added now. The session model must only leave room for a later rolling summary associated with a source boundary such as through_turn_id.

The future design will:

- calculate pressure from total assembled-context tokens;
- prefer model/provider reported token usage when available;
- preserve a recent tail by token budget rather than turn count;
- store the new rolling summary successfully before excluding or deleting covered source turns;
- keep explicit long-term memories outside the rolling summary;
- delete compacted raw turns instead of archiving them to S3.

No placeholder compactor, model abstraction, summary item, or unused interface should be implemented in this branch.

## Minimal failure behavior

- A failed DynamoDB write returns an error and is not reported as persisted.
- A duplicate append of identical turn content is idempotent.
- A duplicate turn ID with different content is rejected.
- Expired turns are not returned.
- Reset must not expose or modify another user's session.
- A pointer with no turns is a normal state. The pointer holds a session ID; there is no separate
  session target row to recover.
- User deletion removes only that user's partition and can be safely retried.

Do not add generalized retries, distributed locks, outbox processing, migrations, background workers, or recovery frameworks in this first feature.

## Verification

Keep tests focused on the actual contracts:

1. Two users cannot read or mutate each other's sessions or turns.
2. Completed turns are returned in chronological order, including two turns that share a timestamp.
3. Identical append replay is idempotent and conflicting replay is rejected.
4. Expired turns are filtered before asynchronous TTL deletion.
5. Reset moves the pointer and the new session reads none of the previous session's turns.
6. User deletion removes the pointer and all turn pages without touching another user.
7. A turn over the configured size limit is rejected before the write.
8. Repository access uses keyed operations and does not Scan.
9. Infrastructure template defines only the required keys, TTL, encryption-at-rest default, and on-demand billing unless review finds a blocker.

## Review questions

Please identify only concrete blockers or material design defects:

1. Is cross-user isolation structurally enforced by every access path?
2. Is the completed-turn boundary sufficient without persisting internal model activity?
3. Are the DynamoDB keys and five access patterns minimal and viable without a GSI?
4. Are TTL and application-side expiry filtering correct?
5. Does idempotent append avoid both duplicate writes and accidental overwrite?
6. Does the plan remain compatible with future token-based compaction and separate explicit memory?
7. Is the responsibility boundary between PIA and pia-harness clear?
8. Is anything in scope speculative or overengineered for the first feature?
9. What is the smallest test set needed before implementation?

If a blocker exists, propose the smallest correction. Do not add future framework features or production-scale machinery to this branch.

## Review record: 2026-09-06, Claude, commit 02c5ab9

Plan-only review of the questions above. Nothing was implemented and nothing was merged.

Two blockers were found and are corrected in the text above.

The append guarantee did not hold. The turn sort key contains a timestamp, but the data model never
said who produces created_at. A repository-generated timestamp puts a retry at a different key, so
the conditional write never fires, and both the idempotent-replay rule and the conflicting-turn-ID
rule fail. created_at is now caller-supplied and part of the retry contract.

The owner follow-up after this review removed created_at from the key entirely. A fixed-width,
time-sortable turn ID is now generated once when the message is accepted and reused for persistence
retries, so turn-ID uniqueness and chronological Query order use the same key.

The active session was not unique. Session rows keyed by session ID force get-or-create to read every
session the user ever had and filter by status, which lets two concurrent calls create two active
sessions and grows without bound because session rows have no TTL. A single pointer row replaces them;
status, CLOSED rows and updated_at are gone, since no access pattern read them.

Smaller corrections: expires_at must be epoch seconds or the TTL service silently never fires; a Limit
combined with a filter returns short pages; timestamps need a fixed width for sort order to mean
anything; a turn needs a size bound because the 400 KB item limit would otherwise drop an answer the
user already received. A caution from the sibling project: an orphaned pointer to a TTL-deleted target
once broke every later reissue there, so absence is written here as a recovery path rather than an
error.

The remaining questions were sound as written. Cross-user isolation holds for turns, the completed-turn
boundary matches the delivery-then-persist rule already used in the agent project, the four access
patterns need no secondary index, and the plan stays compatible with later token-based compaction. Raw
turns expire in fourteen days, so adding fields later needs no migration.

## Owner decisions: 2026-09-06

1. Per-user deletion is included. Account closure must remove all harness data immediately rather than
   wait for the fourteen-day TTL.
2. Reset is retained as a small user-control feature. It changes the active session ID, so previous
   turns leave the model context immediately while their raw records expire normally. Explicit
   long-term memory will not be affected by reset.
3. Namespace is removed. The dedicated table name already separates environments and consumers.
4. The implementation must include the core capabilities above without speculative prevention layers.
   In particular, do not add generalized retries, locks, outboxes, workers, recovery ledgers, unused
   provider abstractions, or future migrations.
