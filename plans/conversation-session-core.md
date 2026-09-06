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

Proposed partition boundary:

- PK: namespace and opaque user key
- SK for session metadata: session and session ID
- SK for turns: session, session ID, timestamp, and turn ID

The library must not require an email address, Telegram ID, Cognito subject, or other direct identifier. It must not log message content or user keys.

## Data model

### Session

- namespace
- user_key
- session_id
- status: ACTIVE or CLOSED
- created_at
- updated_at

### Completed turn

- namespace
- user_key
- session_id
- turn_id
- user_message
- assistant_message
- created_at
- expires_at

Turn identifiers are unique within a user's session. Timestamps are stored in UTC. Message content is treated as opaque text.

## Access patterns

The first feature supports only:

1. Get or create the active session for a user.
2. Append a completed turn to that user's active session.
3. Query recent unexpired turns for that user's session in chronological order.
4. Close the active session and create a replacement during reset.

The design must not require Scan operations.

## Retention

Each completed turn receives an expires_at value calculated from the configurable retention period, defaulting to 14 days.

DynamoDB TTL performs physical deletion asynchronously. Reads must therefore exclude expired turns even when DynamoDB has not removed them yet.

Reset closes the current session and creates a new session. It does not define or delete future long-term memories. Account-wide deletion is outside this feature.

## Write boundary

The caller appends a turn only after the assistant response has been successfully delivered. The harness does not decide whether delivery succeeded.

An append retry for the same user, session, and turn ID must not create a duplicate turn. Conflicting reuse of a turn ID must fail rather than overwrite different content.

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

Do not add generalized retries, distributed locks, outbox processing, migrations, background workers, or recovery frameworks in this first feature.

## Verification

Keep tests focused on the actual contracts:

1. Two users cannot read or mutate each other's sessions or turns.
2. Completed turns are returned in chronological order.
3. Identical append replay is idempotent and conflicting replay is rejected.
4. Expired turns are filtered before asynchronous TTL deletion.
5. Reset closes the old active session and creates a new isolated session.
6. Repository access uses Query/Get/Put-style key access and does not Scan.
7. Infrastructure template defines only the required keys, TTL, encryption-at-rest default, and on-demand billing unless review finds a blocker.

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
