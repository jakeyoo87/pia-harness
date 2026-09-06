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

## Scope

1. Minimal Python package structure.
2. Conversation session and completed-turn domain types.
3. Create or retrieve the active session for one authenticated user.
4. Append one completed turn.
5. Read recent, unexpired turns in chronological order.
6. Reset by closing the active session and creating a new active session.
7. DynamoDB-backed repository.
8. Minimal dedicated DynamoDB table definition with partition key, sort key, and TTL only.
9. Configuration for table name and raw-turn retention days.
10. Focused DynamoDB Local tests for isolation, ordering, reset, and expiry filtering.

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

Every repository operation requires both a namespace and user key. There is no interface that loads a session or turn by session ID alone.

Partition boundary:

- PK: namespace and opaque user key
- SK for the active session pointer: a single fixed value per user, holding the current session ID
- SK for turns: session, session ID, created_at, and turn ID

The namespace is bound when the repository is constructed, not passed per call, so no call site
can reach another namespace by mistake.

The library must not require an email address, Telegram ID, Cognito subject, or other direct identifier. It must not log message content or user keys.

## Data model

### Active session pointer

- namespace
- user_key
- session_id
- created_at

Exactly one pointer exists per user. There is no CLOSED session row: no access pattern reads one,
and unbounded session rows would make every get-or-create read a growing history. The pointer
carries no TTL, and updated_at is not written per turn because nothing reads it.

### Completed turn

- namespace
- user_key
- session_id
- turn_id
- user_message
- assistant_message
- created_at
- expires_at

Turn identifiers are unique within a user's session. Message content is treated as opaque text.

created_at is supplied by the caller together with the turn, not generated inside the repository.
A retry must present the same created_at, because it is part of the item key; a repository-generated
timestamp would place a retry at a different key and defeat the append guarantee below. expires_at
is derived from created_at and the retention setting, so a retry produces an identical item.

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

The design must not require Scan operations, and none of the four needs a secondary index.

## Retention

Each completed turn receives an expires_at value calculated from the configurable retention period, defaulting to 14 days.

DynamoDB TTL performs physical deletion asynchronously. Reads must therefore exclude expired turns even when DynamoDB has not removed them yet.

Reads that apply a limit must filter expired turns first and then take the limit. A DynamoDB
Limit is applied before any filter, so combining the two returns fewer turns than requested.

The pointer must never carry a TTL. Turns expire while the pointer stays, which is intended: a user
returning after the retention window keeps the same session with no turns.

Reset moves the pointer to a new session ID. It does not define or delete future long-term memories,
and it does not delete the previous session's turns, which remain until they expire.

Deleting every turn for one user is not part of the four access patterns above. See the open
decisions at the end of this plan before this library is connected to real member traffic.

## Write boundary

The caller appends a turn only after the assistant response has been successfully delivered. The harness does not decide whether delivery succeeded.

An append retry for the same user, session, turn ID and created_at must not create a duplicate
turn. Conflicting reuse of a turn ID must fail rather than overwrite different content. Both follow
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
- A pointer whose session has no turns left is a normal state, not an error. Absence of a target is
  a recovery path: get-or-create returns the pointer, and a missing pointer creates a new session.

Do not add generalized retries, distributed locks, outbox processing, migrations, background workers, or recovery frameworks in this first feature.

## Verification

Keep tests focused on the actual contracts:

1. Two users cannot read or mutate each other's sessions or turns.
2. Completed turns are returned in chronological order, including two turns that share a timestamp.
3. Identical append replay is idempotent and conflicting replay is rejected.
4. Expired turns are filtered before asynchronous TTL deletion.
5. Reset moves the pointer and the new session reads none of the previous session's turns.
6. A turn over the configured size limit is rejected before the write.
7. Repository access uses Query/Get/Put-style key access and does not Scan.
8. Infrastructure template defines only the required keys, TTL, encryption-at-rest default, and on-demand billing unless review finds a blocker.

## Review questions

Please identify only concrete blockers or material design defects:

1. Is cross-user isolation structurally enforced by every access path?
2. Is the completed-turn boundary sufficient without persisting internal model activity?
3. Are the DynamoDB keys and four access patterns minimal and viable without a GSI?
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

## Open decisions

These need an owner's answer rather than a code change.

1. Per-user deletion. A fourteen-day TTL is not the same as deletion on request, and the member-facing
   consent document in the agent project promises removal at account closure. Adding delete_all_for_user
   now is one method and one test over the existing keys; adding it after integration reopens the
   deletion path. Decide whether it enters this feature, or whether this library stays disconnected from
   real member traffic until it exists.
2. Reset wording. The Bot answers /reset with a message about clearing the conversation context, while
   this plan keeps the previous turns until they expire. Either the wording or the retention should move.
3. Namespace. Configuration already carries the table name, which separates environments. Unless a named
   consumer needs several namespaces inside one table, the plan's own rule against unused interfaces
   argues for dropping it. Cheap to decide now, since no data exists yet.
