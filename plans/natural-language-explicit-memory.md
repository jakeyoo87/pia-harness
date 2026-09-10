# Natural-Language Explicit Memory Plan

## Goal

Let a user remember, correct, forget, or inspect long-term Memory through ordinary conversation. The
normal answer model returns the user-facing answer and one hidden Memory action in the same generation;
there is no separate intent-model call and no command parser.

Explicit updates must join the existing automatic Memory pipeline. They reuse the same
`AutomaticMemoryReviewer`, reviewer callable, `_apply` validation, 4,000-character bound, conditional
Memory write, and change summary. This feature does not create a second Memory store or update path.

Plan author and initial implementation Agent: Codex. Independent plan and implementation reviewer:
Claude. The owner may change either role at any time; the same feature branch remains in use.

## Current state

`pia-harness/main` is clean at `7bde6af` before this plan branch.

- `review_if_due` reviews earlier unreviewed completed Turns on a one-hour revisit.
- `force_review` flushes earlier unreviewed Turns before Compaction and reset.
- `review_explicit_input` reviews earlier unreviewed Turns together with an accepted current input and
  advances Memory to that input's preassigned Turn ID.
- All three enter the same `_apply`, validation, CAS write, and bounded change-summary handling.
- The Orchestrator serializes same-user commits and only applies explicit Memory work for the winning
  generation.
- The Orchestrator currently requires the caller to classify every input through
  `ConversationInput.memory_mode` before answer generation.
- `GeneratedAnswer` currently carries text, model identity, estimated tokens, and optional provider
  usage, but no Memory action.

The missing feature is therefore not another Memory updater. It is the natural-language signal from
the normal answer generation and its routing into the existing updater.

## Confirmed product decisions

- Users speak naturally; there are no `/remember`, `/forget`, categories, keys, or Memory management UI.
- One normal answer-model call returns `answer` plus a hidden `memory_action`.
- No separate intent detector or second intent-only LLM call.
- Ambiguous language is `NONE`; Memory is unchanged. The normal answer may ask a clarifying question.
- Automatic Review timing, Memory storage, 4,000-character limit, CAS behavior, and change-summary
  limits remain unchanged.
- Automatic and explicit Review use one injected `memory_review(request) -> MemoryReviewOutput`
  callable. A later OpenRouter/Nemotron adapter implements that callable once for every Review source.
- Only a successful meaningful Memory write contributes a natural change notice to the current
  response. No separate or late notification is sent.
- Portfolio holdings, balances, transactions, account data, order authority, Risk Check, system policy,
  and market facts remain excluded by the existing reviewer instruction.
- Full Memory deletion requires a separate user confirmation and never relies on an ordinary automatic
  Review emitting `CLEAR`.

## Memory action contract

Replace the caller-supplied `ExplicitMemoryMode` with a generated `MemoryAction`:

    NONE
    UPDATE
    FORGET
    SHOW
    DELETE_ALL

Semantics:

- `NONE`: no explicit Memory operation.
- `UPDATE`: the current user text explicitly asks PIA to remember something or correct an existing
  memory. Run `review_explicit_input(..., allow_clear=False)`.
- `FORGET`: the current user text explicitly asks PIA to forget targeted content. Run
  `review_explicit_input(..., allow_clear=True)`. The reviewer may rewrite the remaining Memory or use
  `CLEAR` if the targeted deletion legitimately removes the final remaining content.
- `SHOW`: no Memory write. The generated answer describes the Memory snapshot already loaded by the
  Assembler for this generation.
- `DELETE_ALL`: a request concerning the entire Memory document. It bypasses the reviewer, but the
  document is deleted only after explicit confirmation as described below.

The action describes the combined winning input batch, not each individual submission. If interruption
combines several user messages, the model saw that exact combined text and returns one action for it.
`UPDATE` or `FORGET` passes the same combined text to `review_explicit_input` under the newest input's
Turn ID. This preserves the invariant that Memory never advances beyond content the reviewer actually
saw.

## Generated answer contract

Extend `GeneratedAnswer` with:

    memory_action: MemoryAction = MemoryAction.NONE
    delete_all_confirmed: bool = False

The default preserves simple existing fake adapters and ordinary-answer behavior while callers migrate.
Validation rejects an unknown action, a non-boolean confirmation flag, or
`delete_all_confirmed=True` for any action other than `DELETE_ALL`.

`delete_all_confirmed` is not confidence scoring. It distinguishes the initial complete-deletion
request from the user's affirmative answer to the immediately preceding confirmation question:

- initial request: `DELETE_ALL`, `delete_all_confirmed=False`; do not delete;
- confirmed follow-up: `DELETE_ALL`, `delete_all_confirmed=True`; delete directly before delivery.

The later model adapter must instruct the model to set the flag only when the current user explicitly
confirms a complete-Memory deletion requested in the immediately preceding conversation. The harness
does not add keyword heuristics. A false or ambiguous confirmation must remain false. Claude should
review whether this is a sufficient first-version confirmation boundary or whether the harness needs a
small explicit pending-confirmation contract before implementation.

## Orchestrator changes

### Input and generation

- Remove `memory_mode` from `ConversationInput`, `submit`, `_conversation_input`, and exports.
- Remove `ExplicitMemoryMode`.
- Validate `GeneratedAnswer.memory_action` after the winning answer returns.
- A superseded or late generation cannot perform its generated Memory action.

### Commit routing

The winning generation claims `COMMITTING` exactly as it does now. Inside the existing per-user
`commit_lock`:

1. Inspect the generated batch-level action.
2. For `UPDATE` or `FORGET`, call `review_explicit_input` once with the exact combined message, newest
   Turn ID, and newest accepted time.
3. Do not first call `review_if_due` for `UPDATE` or `FORGET`: `review_explicit_input` already loads all
   earlier unreviewed Turns and the current input, so one Review covers both the automatic backlog and
   the explicit request through the same `_apply` path.
4. For `NONE`, `SHOW`, and an unconfirmed `DELETE_ALL`, keep the ordinary `review_if_due` behavior.
5. For confirmed `DELETE_ALL`, skip Review and call the existing idempotent `delete_memory` directly.
   Do not append an automatic change summary from content that was just deleted.
6. Continue with the existing Compaction decision, delivery, and completed-Turn append order.

`SHOW` does not need a second DynamoDB read: the answer model received the same user-isolated Memory
snapshot loaded immediately before assembly. It is read-only and never advances the Memory boundary.

The initial unconfirmed `DELETE_ALL` action does not mutate Memory. The answer is expected to ask for
confirmation naturally. A confirmed deletion produces no reviewer change summary; the answer itself
may acknowledge deletion after the direct delete succeeds. If direct deletion fails, set
`memory_failed=True` and do not claim durable success through a harness-generated change notice.

The existing order for ordinary automatic Review, pre-Compaction Review, Compaction, delivery, and Turn
persistence otherwise remains unchanged.

## Common reviewer pipeline

No storage or reviewer fork is added:

    one-hour revisit ───────────────┐
    pre-Compaction / pre-reset ─────┤
    generated UPDATE / FORGET ──────┤
                                    ▼
                         AutomaticMemoryReviewer
                                    ↓
                    load current Memory and unreviewed Turns
                                    ↓
                         one reviewer callable
                                    ↓
                    validate action, 4,000 chars, CLEAR
                                    ↓
                         same boundary CAS write
                                    ↓
                      bounded change summary

The sources differ only in trigger provenance, inclusion of the accepted current input, and CLEAR
permission. `SHOW` and confirmed `DELETE_ALL` are deliberately outside document rewriting.

## Model adapter boundary

This feature defines and consumes the structured answer contract but does not add OpenRouter HTTP,
Nemotron configuration, secrets, provider routing, or model discovery to `pia-harness`.

The next Model Adapter must:

- render the typed Assembler parts without promoting Memory, Summary, history, or current user content
  to trusted system instructions;
- request a strict structured result containing `answer`, `memory_action`, and
  `delete_all_confirmed` from the same normal answer call;
- implement the existing Memory reviewer callable once and use it for automatic and explicit Review;
- map provider usage into the existing `ContextUsage` contract;
- remain cancellable during answer generation so Orchestrator interruption keeps working;
- fail closed on malformed or unsupported structured output without guessing an action.

OpenRouter documents JSON-Schema structured output through `response_format` for compatible endpoints.
The adapter plan must verify model/endpoint support and require compatible routing rather than silently
falling back to loose JSON. That provider work belongs to the subsequent adapter feature so this change
does not add `httpx` or duplicate the existing `pia-agent/app/openrouter.py` client.

## Failure behavior

- Invalid generated action or confirmation flag: generation fails before commit; no Memory write.
- Explicit Review failure or stale CAS: preserve current Memory and boundary, set `memory_failed=True`,
  and continue the answer path without a successful change notice.
- Automatic Review failure remains non-blocking as today.
- `SHOW` never mutates Memory even if the answer model describes it incorrectly.
- Unconfirmed or ambiguous full deletion never calls `delete_memory`.
- Confirmed direct deletion failure preserves the item and sets `memory_failed=True`.
- Delivery failure does not roll back a successful explicit Memory update or confirmed deletion, matching
  the existing commit-local failure boundary; no retry, outbox, or cross-response notice is added.
- Completed-Turn persistence failure does not roll back earlier Memory work.

## Tests

Update focused Orchestrator tests and then run the complete suite against DynamoDB Local.

Required cases:

1. ordinary generated `NONE` performs no explicit Review;
2. `UPDATE` reviews the exact combined winning input once with `allow_clear=False`;
3. `FORGET` reviews the exact combined winning input once with `allow_clear=True`;
4. a due backlog plus `UPDATE` or `FORGET` is passed through one explicit Review, not an automatic
   Review followed by a duplicate explicit Review;
5. `SHOW` performs no explicit write and ordinary due Review behavior remains intact;
6. unconfirmed `DELETE_ALL` does not delete Memory;
7. confirmed `DELETE_ALL` directly and idempotently deletes Memory without reviewer invocation;
8. an unknown action or invalid confirmation combination fails before commit;
9. a superseded answer's action never runs, including when provider cancellation is ignored;
10. a message arriving during `COMMITTING` queues and cannot interleave with Memory work;
11. change summaries remain unique, bounded to three, and attached only after a successful write;
12. explicit Review, delete, delivery, and Turn-persistence failures retain the existing documented
    result boundaries;
13. different users still proceed independently;
14. `ConversationInput.memory_mode` and `ExplicitMemoryMode` no longer remain in the public API.

No live model call, credential, AWS mutation, deployment, or Bot restart is part of verification.

## Documentation

Update `README.md` after implementation to state that natural-language Memory actions come from the
generated answer rather than a caller-supplied mode. Keep provider integration explicitly separate.

No `pia-agent` document changes are made on this plan-only branch. The later adapter and integration
features update `/opt/pia/docs/02-ai-conversation.md` when runtime behavior actually changes.

## Out of scope

- a separate intent model or string/regex command parser;
- automatic importance scores, confidence thresholds, categories, keys, history, backup, or rollback;
- embeddings, vector search, Memory administration UI, or administrator access;
- provider client, fallback hierarchy, retry framework, tool calling, or Agent SDK;
- Telegram or `pia-agent` integration;
- AWS table, IAM, Secrets Manager, deployment, or operating Bot changes;
- durable confirmation state, queue, worker, outbox, or distributed coordination.

## Implementation order after review

1. Add and export `MemoryAction`; extend and validate `GeneratedAnswer`.
2. Remove caller-supplied `ExplicitMemoryMode` from input and submission APIs.
3. Route winning batch-level actions in `_commit_response` through the existing reviewer or direct
   confirmed deletion path.
4. Update focused concurrency, Memory, failure, and public-contract tests.
5. Update `README.md` and run the full suite once.
6. Commit and push implementation on this same branch for Claude's final review.

## Claude review questions

1. Does moving classification from `ConversationInput.memory_mode` to the winning
   `GeneratedAnswer.memory_action` preserve interruption correctness and prevent superseded actions?
2. Is one batch-level action over the exact combined message the smallest correct contract?
3. Is skipping `review_if_due` for `UPDATE` and `FORGET` correct because `review_explicit_input` already
   consumes all prior unreviewed Turns through the current input?
4. Do `NONE`, `UPDATE`, `FORGET`, `SHOW`, and `DELETE_ALL` cover the agreed natural experience without
   an unnecessary `AMBIGUOUS` state?
5. Is `delete_all_confirmed` sufficient and safe for the first version without durable confirmation
   state, or should complete deletion remain deferred to `pia-agent` integration?
6. Does `SHOW` correctly rely on the Memory snapshot already included by the Assembler without another
   store read or boundary advance?
7. Can an explicit Review failure make the generated answer falsely imply that Memory was saved, and if
   so, what is the smallest provider-independent correction without a second answer-model call?
8. Is any proposed validation, state, or test unnecessary, or is any existing invariant left uncovered?

If a blocker exists, propose the smallest correction. Do not add an intent-only LLM call, command
parser, Memory store, approval queue, provider hierarchy, worker, queue, outbox, vector database,
distributed lock, AWS change, or deployment work.
