# Persistence and data

## Table contract

`DynamoDBConversationStore` uses one DynamoDB-compatible table with:

- string partition key: `pk`;
- string sort key: `sk`;
- TTL attribute: `expires_at`, applied only to raw Turn items;
- consistent reads for active session, Memory, Summary, and Turn queries.

The consuming application creates the table, enables TTL, supplies the low-level boto3 DynamoDB client,
and grants IAM permissions. The Harness does not create or discover infrastructure.

Every item for one user is isolated under:

```text
pk = USER#{opaque_user_key}
```

Never use an email address, channel username, account number, or other direct personal identifier as
`user_key`.

## Item map

| Purpose | `sk` | Important attributes | TTL |
| --- | --- | --- | --- |
| Active session | `ACTIVE_SESSION` | `session_id`, `created_at` | none |
| Completed Turn | `TURN#{session_id}#{turn_id}` | `session_id`, `turn_id`, `user_message`, `assistant_message`, `created_at`, `expires_at` | default 30 days |
| Rolling Summary | `SUMMARY#{session_id}` | `session_id`, `summary_text`, `through_turn_id`, `summary_tokens`, `model_id`, `updated_at` | none |
| Long-term Memory | `MEMORY` | `memory_text`, `last_reviewed_turn_id`, `updated_at` | none |

The item names are exact current implementation contracts. There is no GSI, category item, Memory
history, session-history item, archive, or S3 copy.

## Turn identity and ordering

`new_turn_id(created_at)` creates:

```text
YYYYMMDDTHHMMSSffffffZ_{32 lowercase hex characters}
```

The timestamp prefix makes IDs sortable and the random suffix prevents collisions at one timestamp.
`append_completed_turn` requires the ID timestamp to match the timezone-aware `created_at` value.

A completed Turn contains one combined user batch and the delivered assistant response. The default
maximum combined UTF-8 content size is 256 KiB. The default TTL is `created_at + 30 days` and can be
changed through `DynamoDBConversationStore(retention_days=...)`.

Turn append is idempotent:

- first write uses `attribute_not_exists(pk) AND attribute_not_exists(sk)`;
- the same key and exactly the same item can be replayed successfully;
- the same Turn ID with different content raises `TurnConflictError`.

Expired Turn items are filtered at read time even if DynamoDB TTL deletion has not run yet.

## Active session and reset

`get_or_create_active_session` creates one pointer item conditionally. Concurrent creators return the
winner.

Reset performs:

```text
best-effort forced Memory Review
→ conditionally replace ACTIVE_SESSION.session_id
→ delete the old session's SUMMARY item
```

The active pointer update requires the caller's expected current session ID. A changed pointer raises
`SessionConflictError` rather than overwriting the winner.

Reset does not synchronously delete old raw Turns; they become unreachable from the new active session
and expire under their existing TTL. Long-term Memory is preserved. The Orchestrator also clears its
in-process complete-deletion confirmation state.

## Context loading and Summary boundary

`load_context(user_key, session_id)` returns:

- the one current `SUMMARY#{session_id}`, if present;
- only unexpired raw Turns whose `turn_id` is strictly greater than `summary.through_turn_id`.

This prevents a summarized Turn from appearing twice in model context even when physical deletion of a
covered Turn has not completed.

Summary replacement uses compare-and-set on the previously observed `through_turn_id`, or requires item
absence for the first Summary. A lost CAS returns false and leaves the winner intact. Covered raw Turns
are deleted only after the Summary write succeeds.

## Long-term Memory boundary

One `MEMORY` item stores a complete free-form document of at most 4,000 Unicode characters.
`last_reviewed_turn_id` means every eligible Turn at or below that boundary has already been presented to
the winning Memory Review.

Memory replacement uses compare-and-set on the previously observed boundary, or item absence for the
first write. A lost CAS is returned as a stale result and never overwrites newer Memory.

An empty Memory document is valid and still carries a boundary. Confirmed “delete all Memory” therefore
writes:

```text
memory_text = ""
last_reviewed_turn_id = newest accepted input Turn ID
```

It does not delete the item. Removing the boundary would allow retained raw Turns to repopulate content
the user just deleted. `delete_memory` remains a low-level operation; account closure uses partition-wide
deletion.

## Unreviewed Turn loading

`load_unreviewed_turns` intentionally ignores the rolling Summary boundary. It queries unexpired raw
Turns strictly after `last_reviewed_turn_id`, because Memory Review and Conversation Summary are separate
purposes.

Compaction can physically remove an unreviewed Turn if the preceding best-effort Memory Review fails.
Reset can make an old session's unreviewed Turns unreachable. Conversation availability and explicit
reset take priority; there is no outbox, recovery ledger, or automatic retry loop at the persistence
layer.

## User deletion

`delete_all_for_user(user_key)` queries keys only inside `USER#{user_key}` and deletes every item in that
partition, including active session, all session Turns and Summaries, and Memory. It is repeatable and
does not scan or touch another user's partition.

The consuming application remains responsible for invoking this method as part of its complete member
withdrawal workflow and for deleting data in its other stores.

## Failure and consistency summary

| Operation | Protection | Failure result |
| --- | --- | --- |
| Active session create | item-absence condition | return concurrent winner or raise store error |
| Turn append | item-absence condition + exact replay check | idempotent replay or `TurnConflictError` |
| Session reset | expected session ID | `SessionConflictError` |
| Memory replace | expected review boundary | stale result, winner preserved |
| Summary replace | expected summary boundary | no replacement, winner preserved |
| Context load | Summary boundary + expiry filter | no duplicate or expired Turn in returned context |
| User deletion | partition-scoped query | repeatable count of removed items |

These guarantees are single-table data guarantees. In-flight model generation and same-user commit order
are handled separately by the in-process Orchestrator.
