# Conversation lifecycle

## End-to-end request flow

`ConversationOrchestrator.submit` accepts one opaque user key and one non-empty message. It assigns a
sortable Turn ID immediately and coordinates the complete request:

```text
accept current input
→ get/create active session
→ load long-term Memory and boundary-aware Conversation Context
→ assemble trusted system prompt + untrusted data
→ if input budget overflows: force Memory Review, compact once, reassemble once
→ generate structured Answer + MemoryAction (optionally one host tool request)
→ claim commit ownership
→ execute the selected host tool, when configured
→ apply due or explicit Memory work
→ compact after response when the trigger is reached
→ append successful Memory change notices or required failure notice
→ deliver through the caller's channel callable
→ append the completed combined Turn (host-controlled stored text for a tool Turn)
```

The Adapter can retry inside one model operation, but the Orchestrator sees one final success or error.
No model output causes persistence or delivery until the winning generation claims commit ownership.
No tool callback runs before that claim. A tool request cannot combine with a Memory action or Web
Search. The host owns tool arguments, authorization, timeout, result wording, and any external
idempotency; Harness owns only the call's position in the conversation lifecycle.

## Context assembly

The Assembler emits ordered `PromptContextPart` values:

```text
SYSTEM
MEMORY, when non-empty
SUMMARY, when present
USER_TURN / ASSISTANT_TURN pairs after the Summary boundary
CURRENT_USER
```

Only `SYSTEM` is trusted instruction. Every other part is untrusted data. Before counting, the Assembler
validates:

- Memory belongs to the requested user;
- Summary belongs to the requested user and session;
- every Turn belongs to the requested user and session;
- Turn IDs are strictly increasing;
- no returned Turn overlaps the Summary boundary;
- required text and token-budget values are valid.

The Adapter-supplied counter measures the rendered Answer request, including its structured-output
schema. The Assembler never truncates or compacts. If required input exceeds
`ModelTokenBudget.input_tokens`, it raises `ContextBudgetExceeded` containing counts only.

## Shared token budget and Compaction

One immutable budget supplies:

```text
input_tokens = context_limit - response_tokens
compaction trigger = floor(input_tokens × 0.90)
protected tail budget = floor(context_limit × 0.125)
Summary output limit = response_tokens
```

After a successful answer, provider `total_tokens` is preferred when its model ID matches the answer
model ID. Otherwise the conservative fallback counts UTF-8 bytes. The fallback may compact early but
must not let context overflow silently.

When the trigger is reached:

1. Load the current Summary and unexpired raw Turns.
2. Validate store identity, strict Turn order, Summary boundary, and expiry; fail closed on violation.
3. Preserve the newest Turn regardless of size.
4. Preserve as much recent tail as fits the protected-tail budget.
5. Summarize the previous Summary plus older covered Turns.
6. Validate a non-empty, smaller Summary and its optional provider token count.
7. CAS-write one replacement Summary.
8. Delete raw Turns through the winning `through_turn_id` only after the write succeeds.

The store owns the physical Summary and Turn representation. Harness validates the returned sequence
because a bad boundary could otherwise delete a Turn that was not summarized.

On assembly overflow, the Orchestrator makes at most one Compaction and one reassembly attempt. A still
oversized batch returns `CONTEXT_OVERFLOW` and clears that unprocessable pending batch so later short
messages are not permanently stranded.

## Automatic long-term Memory

`AutomaticMemoryReviewer` keeps one complete document per user. The same injected reviewer callable is
used by every source:

```text
one-hour revisit ─────────────┐
pre-Compaction / pre-reset ───┤
explicit UPDATE / FORGET ─────┤
                              ▼
                    review_memory(request)
                              ↓
          validate action, 4,000 chars and CLEAR permission
                              ↓
                    conditional boundary write
```

Automatic Review is evaluated when the user returns after at least one hour and earlier unreviewed
completed Turns exist. There is no idle timer, Cron, worker, queue, or Turn-count trigger.

Before Compaction and reset, `force_review` reviews any unreviewed current-session Turns. Failure is
best-effort and does not block Compaction or reset; `ConversationAbandoned` still stops both.

The reviewer retains durable communication preferences, investment horizon, user-authored theses,
corrections, constraints, and decisions. It excludes transient prices/news, public facts, external text
misattributed to the user, credentials, account data, holdings, balances, transactions, system policy,
Risk Check, and order authorization.

Output actions:

- `UNCHANGED`: keep the current document and advance the boundary; no replacement text or change notice.
- `REPLACE`: store one complete non-empty replacement document; never a patch.
- `CLEAR`: store an empty document only when explicit targeted forgetting allows it.

Meaningful changes may return up to three unique user-facing notices of at most 200 characters each.
They are ephemeral response content, not Memory history.

## Natural-language explicit Memory

The normal Answer call returns one batch-level `MemoryAction`:

- `NONE`: ordinary conversation or Memory-description question;
- `UPDATE`: explicit request to remember or correct durable context;
- `FORGET`: explicit targeted forgetting;
- `DELETE_ALL`: complete Memory deletion request or confirmation.

There is no keyword parser or intent-only model call. Ambiguous language stays `NONE`, and the Answer may
clarify naturally.

For `UPDATE` and `FORGET`, the Orchestrator calls `review_explicit_input` once with:

- all earlier unreviewed completed Turns;
- the exact combined winning input batch;
- the newest accepted input's Turn ID and timestamp;
- `allow_clear=False` for `UPDATE`, `True` for `FORGET`.

It does not first call `review_if_due`, because the explicit method already consumes the backlog and
current input in one review. This avoids duplicate model calls and advances the boundary only past content
the reviewer actually saw.

Complete deletion requires two delivered generations. The first unconfirmed `DELETE_ALL` arms one
in-process per-user confirmation marker. A later confirmed action is honored only while that marker is
present. Restart or reset loses the marker and safely requires confirmation again. Successful deletion
writes an empty Memory document at the newest input boundary; it does not remove the item.

When the OpenRouter Adapter is configured with Web Search and the Answer requests search, it returns
`NONE` instead of `DELETE_ALL`, so neither stage arms nor confirms complete deletion. `UPDATE` and
`FORGET` are unchanged. See [Model Adapter and integration](04-model-adapter-and-integration.md).

An application may opt into best-effort progress delivery. The Adapter reports only
`WEB_SEARCH_STARTED` and `WEB_SEARCH_RETRYING`; the Orchestrator adds `COMPLETE` after final delivery or
generation termination. Progress carries the opaque newest Turn ID so a consuming channel can ignore a
late event from a superseded generation. The legacy one-argument Answer callable remains supported.

## Interruption and commit ownership

Per-user coordination has three phases:

| Phase | New message behavior |
| --- | --- |
| `IDLE` | starts generation |
| `GENERATING` | supersedes the current submission, requests cancellation, combines all pending text, starts a newer generation |
| `COMMITTING` | queues for the next generation; the owned commit is not interrupted |

Generation ID is the correctness mechanism. A late provider result cannot claim commit if a newer
generation owns the user state, even when provider cancellation was ignored.

Within one user's commit lock, Memory Review, confirmed Memory clearing, Compaction, delivery, and
completed-Turn persistence cannot interleave with another commit. Different users use separate states and
can proceed concurrently.
After commit ownership is claimed, even repeated external task cancellation keeps waiting through a
shield for the owned commit result instead of abandoning an in-flight host tool or leaving the user's
state stuck in COMMITTING.

Pending messages combined after interruption are stored as one completed Turn under the newest input's
Turn ID, separated by `MESSAGE_SEPARATOR`. Only the newest submitter receives the delivered result; earlier
superseded submitters receive `SUPERSEDED`.
For a tool Turn, the host returns separate delivery text and persisted user/assistant text. This lets
an application deliver account-specific results without adding their raw values to later Memory Review
or Compaction. Ordinary Answers keep the existing raw combined Turn behavior.

## Delivery and failure boundaries

Final delivery happens before completed-Turn persistence. This is intentional:

- once delivery succeeds, the user has received the answer and the pending batch is cleared;
- if Turn append then fails, return `PERSISTENCE_FAILED` without answering the same batch again;
- successful Memory or Compaction work is not rolled back after delivery failure or Turn failure;
- there is no outbox, retry payload, rollback, or cross-response change notice.

`ConversationResult` statuses:

| Status | Meaning |
| --- | --- |
| `DELIVERED` | answer delivered and completed Turn persisted |
| `SUPERSEDED` | a newer generation or reset replaced this submission |
| `ABANDONED` | the application vetoed the conversation; pending input is cleared without retry or notice |
| `CONTEXT_OVERFLOW` | one compact/reassemble attempt could not fit the batch |
| `GENERATION_FAILED` | assembly or model generation failed before commit/delivery |
| `DELIVERY_FAILED` | final text could not be delivered; pending input remains eligible |
| `PERSISTENCE_FAILED` | delivery succeeded but completed Turn append failed |
| `TOOL_FAILED` | host tool callback raised unexpectedly; no delivery or Turn append, and pending input is cleared |

The host should return a safe `ToolResult` for expected failures, including an ambiguous external
outcome. Harness does not retry a tool call. If delivery fails after a successful tool callback,
the pending input remains eligible for regeneration; an effectful host tool must therefore provide
its own stable idempotency before it is enabled. This first read-tool stage does not add source IDs.

`memory_failed` and `compaction_failed` report non-blocking side-operation failures. A required localized
explicit-Memory failure notice is appended when an explicit update, forget, or confirmed clear fails or
loses CAS; automatic Memory failure stays silent.

`ConversationAbandoned` is treated consistently during reads, Memory, Compaction, delivery, and final
Turn append. It is never swallowed by a best-effort catch or converted to an ordinary failure. Even when
delivery already completed, the batch is cleared so it cannot be delivered again; the delivery port
remains the application's source of truth for what the user saw.

## Reset

`reset(user_key)`:

1. marks reset requested and supersedes generation or queued submissions;
2. waits for any owned commit through the same commit lock;
3. attempts one best-effort forced Memory Review;
4. conditionally creates a new active session and removes the old Summary;
5. clears pending input and deletion-confirmation state;
6. leaves long-term Memory intact.

Callers map reset and result statuses to their channel UX. The Harness contains no slash-command parser.
Reset always restores its in-process state in a `finally` block, including when the store or Memory
Reviewer abandons or fails, so later submissions are not permanently superseded. A veto re-raises
`ConversationAbandoned` to the caller; a veto during the forced Review stops reset before the active
session is replaced.
