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
    DELETE_ALL

Semantics:

- `NONE`: no explicit Memory operation.
- `UPDATE`: the current user text explicitly asks PIA to remember something or correct an existing
  memory. Run `review_explicit_input(..., allow_clear=False)`.
- `FORGET`: the current user text explicitly asks PIA to forget targeted content. Run
  `review_explicit_input(..., allow_clear=True)`. The reviewer may rewrite the remaining Memory or use
  `CLEAR` if the targeted deletion legitimately removes the final remaining content.
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
- confirmed follow-up: `DELETE_ALL`, `delete_all_confirmed=True`; conditionally write an empty Memory
  document at the newest input boundary before delivery.

The later model adapter must instruct the model to set the flag only when the current user explicitly
confirms a complete-Memory deletion requested in the immediately preceding conversation. The harness
does not add keyword heuristics. The Orchestrator independently requires its existing per-user state to
show that the immediately preceding delivered generation produced an unconfirmed `DELETE_ALL`.
Otherwise it treats the action as unconfirmed and asks again. This state is in-process only: a restart
loses it and safely requires confirmation again.

## Orchestrator changes

### Input and generation

- Remove `memory_mode` from `ConversationInput`, `submit`, `_conversation_input`, and exports.
- Remove `ExplicitMemoryMode`.
- Validate `GeneratedAnswer.memory_action` after the winning answer returns.
- A superseded or late generation cannot perform its generated Memory action.
- Add one required caller-supplied explicit-Memory failure notice. It uses the existing response
  composition path and keeps wording outside this provider- and channel-independent harness.

### Commit routing

The winning generation claims `COMMITTING` exactly as it does now. Inside the existing per-user
`commit_lock`:

1. Inspect the generated batch-level action.
2. For `UPDATE` or `FORGET`, call `review_explicit_input` once with the exact combined message, newest
   Turn ID, and newest accepted time.
3. Do not first call `review_if_due` for `UPDATE` or `FORGET`: `review_explicit_input` already loads all
   earlier unreviewed Turns and the current input, so one Review covers both the automatic backlog and
   the explicit request through the same `_apply` path.
4. For `NONE` and an unconfirmed `DELETE_ALL`, keep the ordinary `review_if_due` behavior.
5. Honor confirmed `DELETE_ALL` only when the same user's immediately preceding delivered generation
   produced an unconfirmed `DELETE_ALL`. Skip Review and conditionally write an empty Memory document
   with `last_reviewed_turn_id` set to the newest pending input's Turn ID. Use the observed Memory
   boundary as the `replace_memory` CAS expectation. Do not remove the item or append an automatic
   change summary from content that was just cleared.
6. Continue with the existing Compaction decision, delivery, and completed-Turn append order.

Memory-description questions remain `NONE`: the answer model received the same user-isolated Memory
snapshot loaded immediately before assembly, so no `SHOW` action or second read is necessary.

The initial unconfirmed `DELETE_ALL` action does not mutate Memory. After its answer is delivered, mark
the per-user coordination state as awaiting confirmation and keep that state from idle cleanup. Any
other delivered action clears the marker. A confirmed clear produces no reviewer change summary; the
answer itself may acknowledge deletion after the conditional empty write succeeds. If that write or an
explicit UPDATE/FORGET Review fails or returns stale, set `memory_failed=True` and append the required
failure notice. Never report a successful Memory change notice for a failed write.

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
permission. Confirmed `DELETE_ALL` bypasses the LLM reviewer but preserves the same bounded-document
and CAS model by writing an empty document at the newest boundary.

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
- Unconfirmed or ambiguous full deletion never calls `delete_memory`.
- Confirmed clear writes an empty document at the newest boundary, so later Review cannot rebuild
  Memory from older retained Turns.
- A confirmation without the preceding delivered unconfirmed-delete marker is not honored.
- Explicit Review or confirmed-clear failure preserves the item, sets `memory_failed=True`, and appends
  the required caller-supplied failure notice.
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
5. a Memory-description question uses `NONE`, performs no explicit write, and ordinary due Review
   behavior remains intact;
6. unconfirmed `DELETE_ALL` does not clear Memory and retains its per-user state after delivery;
7. confirmed `DELETE_ALL` is honored only after the preceding delivered unconfirmed action and writes
   an empty Memory document at the newest boundary without reviewer invocation;
8. a restart-equivalent missing confirmation marker safely asks again rather than clearing;
9. the next automatic or forced Review cannot rebuild cleared Memory from Turns below the new boundary;
10. an unknown action or invalid confirmation combination fails before commit;
11. a superseded answer's action never runs, including when provider cancellation is ignored;
12. a message arriving during `COMMITTING` queues and cannot interleave with Memory work;
13. change summaries remain unique, bounded to three, and attached only after a successful write;
14. explicit Review, clear, delivery, and Turn-persistence failures retain the existing documented
    result boundaries;
15. an explicit stale/failure appends the configured failure notice, while an automatic failure remains
    silent;
16. different users still proceed independently;
17. `ConversationInput.memory_mode`, `ExplicitMemoryMode`, and `SHOW` no longer remain in the public API.

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
- durable confirmation state, queue, worker, outbox, or distributed coordination. The one in-process
  confirmation marker lives only in the existing per-user Orchestrator state.

## Implementation order after review

1. Add and export `MemoryAction`; extend and validate `GeneratedAnswer`.
2. Remove caller-supplied `ExplicitMemoryMode` from input and submission APIs.
3. Route winning batch-level actions in `_commit_response` through the existing reviewer or conditional
   confirmed-clear path, with the in-process preceding-response check.
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

## Resolution record: 2026-09-10, after Claude plan review

The owner accepted the minimal corrections. Drop `SHOW`. Confirmed `DELETE_ALL` is honored only when
the same user's existing Orchestrator state records that the immediately preceding delivered generation
produced an unconfirmed `DELETE_ALL`; the state remains in-process and is discarded on restart. Clearing
Memory conditionally writes an empty document at the newest input boundary instead of deleting the item,
so retained raw Turns cannot repopulate it. Add one required caller-owned explicit-Memory failure notice
and append it through the existing response composition when an explicit Review or confirmed clear
fails or is stale. The common reviewer pipeline, automatic schedule, storage schema, interruption model,
and simple no-worker/no-parser boundary otherwise remain unchanged. Implementation proceeds on this
branch.

## Review record: 2026-09-10, Claude, plan commit c11dd0c

Plan-only review against merged `main` at `7bde6af`. Nothing was implemented and nothing was merged.
Two blockers, one material correction, and one removal follow. The routing design is otherwise right:
taking the action from the winning generation, reusing one reviewer pipeline, and passing the exact
combined text under the newest Turn ID are all correct and need no change.

What holds, checked against the code rather than the prose. Moving classification into
`GeneratedAnswer` strengthens interruption rather than weakening it: a superseded generation never
reaches `_claim_commit`, so its action cannot run, and the action is derived from exactly the text the
model was shown. Skipping `review_if_due` for `UPDATE` and `FORGET` is correct — `review_explicit_input`
already loads every unreviewed Turn after the boundary, passes them as prior turns, and advances the
boundary past all of them, so a second automatic Review would only duplicate work. `SHOW` needs no
store read: `_assemble_with_overflow` loads Memory immediately before assembly and the assembler emits
it as an untrusted part, so the model already has the snapshot.

## Blocker 1: a confirmed full deletion is undone by the next Review

`delete_memory` removes the whole item, including `last_reviewed_turn_id`. `AutomaticMemoryReviewer._load`
then reads `boundary = None`, and `load_unreviewed_turns` treats a `None` boundary as "everything",
returning every non-expired Turn in the session. Raw Turns live for thirty days.

So a user who says "forget everything", confirms, and comes back an hour later hits the ordinary
one-hour revisit Review, which now re-reads up to thirty days of raw conversation with no boundary and
rebuilds a Memory document from exactly the content the deletion was meant to erase. A pre-Compaction
`force_review` can do it even sooner, since it has no timing gate at all. The user is told the Memory is
gone, and it comes back.

The existing design already solved this for the reviewer's own `CLEAR`: that path writes an empty
document and advances the boundary, which is why an emptied Memory stays empty. Confirmed `DELETE_ALL`
as planned regresses that.

Smallest correction: make confirmed deletion write rather than delete.

    Confirmed DELETE_ALL writes an empty Memory document through the existing conditional
    `replace_memory`, with `memory_text=""` and `last_reviewed_turn_id` set to the newest pending
    input's Turn ID, using the observed boundary as the compare-and-set expectation. `delete_memory`
    remains for account closure through `delete_all_for_user`.

This reuses the CAS path the feature already shares, needs no new storage, and keeps the documented and
tested state of an empty document with an advanced boundary. It also makes the deletion concurrency-safe
for free, which a bare delete is not.

## Blocker 2: `delete_all_confirmed` alone is not a confirmation boundary

The flag asks one model call to bind an affirmative like "yes" to a deletion question asked in a
previous turn, and nothing else checks that binding. Two ways it fails. The user may be answering a
different question the assistant asked, and the harness cannot tell. Worse, the question itself may no
longer be in the raw context: if Compaction ran in between, the confirmation prompt survives only inside
the rolling Summary, which is lossy and need not preserve that PIA offered to erase Memory. The model
then sees an affirmative with no antecedent and one wrong classification erases the whole curated
document, with no history and no backup by deliberate design.

Smallest correction, using state the Orchestrator already keeps and adding nothing durable:

    A confirmed DELETE_ALL is honored only when the same user's immediately preceding delivered
    generation produced an unconfirmed DELETE_ALL. Record that on the existing per-user coordination
    state, and keep that state from being reclaimed while a confirmation is outstanding. Otherwise treat
    the confirmation as unconfirmed and ask again.

One field plus one condition in idle cleanup. It is in-process, so a restart loses the pending
confirmation and the user is asked again, which is the safe direction, and it is not the durable
confirmation state this plan's own scope excludes. It also gives unconfirmed `DELETE_ALL` a purpose:
without it, an unconfirmed deletion request behaves exactly like `NONE`.

Deferring complete deletion to the `pia-agent` integration is an equally acceptable answer to review
question 5. Shipping the flag with no second check is not.

## Material correction: an answer can claim a save that failed

The answer text is produced before the Memory write. When `review_explicit_input` raises or loses its
compare-and-set, the plan sets `memory_failed=True` and delivers the generated answer unchanged, so a
reply that says the request was remembered is delivered even though nothing was stored. The caller
cannot repair this after the fact, because the harness owns `deliver` and the answer has already been
sent by the time the result carries `memory_failed`.

Smallest provider-independent correction, with no second model call and no harness-authored wording:

    The Orchestrator accepts one optional caller-supplied failure notice string. When an explicit
    UPDATE or FORGET Review fails, that string is appended to the answer through the existing change
    summary composition. Unset means today's behavior.

The wording stays with the caller that owns the user's language, and it reuses the append path that
already exists for success notices.

## Removal: `SHOW` is indistinguishable from `NONE`

The action is a model output, not an input, and the plan routes `SHOW` exactly as it routes `NONE`: no
write, no boundary change, ordinary due Review. The model can already describe Memory because the
assembler put it in the prompt, whatever value it returns. Nothing in the harness ever branches on
`SHOW`, so it is an enum member that changes no behavior. Drop it and let a description request be
`NONE`.

## Answers to the review questions

1. Yes, and it is stronger than the caller-supplied mode, because only the winning generation can act
   and it acted on exactly the text it saw.
2. Yes, one batch-level action is the smallest correct contract; per-message classification would need a
   second call or a parser, both excluded. One consequence worth writing down: a batch mixing a remember
   and a forget collapses to one action, so choosing `FORGET` grants CLEAR permission over text that also
   asks to remember something. The reviewer's own rule that CLEAR only applies when the targeted removal
   empties the document bounds it, so this is acceptable, not a defect.
3. Yes. `review_explicit_input` consumes the backlog and the current input in one `_apply`, and running
   `review_if_due` first would review the same Turns twice.
4. `NONE`, `UPDATE`, `FORGET` and `DELETE_ALL` cover it; `SHOW` does not earn its place. No `AMBIGUOUS`
   state is needed, since ambiguity is `NONE` plus a clarifying answer.
5. Not sufficient as written; see blocker 2. Either add the preceding-turn check or defer deletion.
6. Yes, and no extra read or boundary advance is needed.
7. Yes, it can; see the material correction above.
8. The validation set is right, and the test list covers the invariants including superseded actions and
   commit-phase queuing. Add one case for blocker 1: after a confirmed deletion, the next Review must not
   rebuild Memory from Turns below the new boundary.

## Instructions for Codex

Apply blocker 1's write-empty-with-boundary rule and blocker 2's preceding-turn check, or defer
`DELETE_ALL` entirely and say so in the plan. Add the optional failure notice, drop `SHOW`, and add the
verification case named above. Nothing else needs to change; no intent-only call, parser, second store,
history, queue, worker, outbox, or provider work should appear during implementation.

## Review record: 2026-09-10, Claude, implementation commit 57a3472

Implementation review against this plan and merged `main` at `7bde6af`. Verdict: no blocker remains.
One defect and one coverage gap were found and fixed on this branch. Nothing was merged, and no AWS,
Bot, or provider change was made.

Verified by running. The suite is 51 tests green against DynamoDB Local, and ruff reports no unused
imports, no undefined names and no bugbear findings.

All four findings from the plan review are implemented faithfully. Confirmed deletion now writes an
empty document at the newest pending Turn ID through the existing conditional `replace_memory` instead
of removing the item, so the boundary survives; the confirmation is honored only when the per-user state
shows a preceding delivered unconfirmed `DELETE_ALL`; the caller-supplied failure notice rides the
existing change-summary composition; and `SHOW` is gone from the enum, the routing, and the exports.

Contracts were checked by mutation rather than only by reading, and each of these fails the suite:
dropping the pending-marker requirement, replacing the empty write with `delete_memory`, leaving the
boundary at the old value instead of the newest Turn ID, adding a `review_if_due` call before an
explicit `UPDATE` or `FORGET`, and keeping the marker instead of clearing it after a delivered action.
Only the winning generation can act, since `_commit_response` is reached only after `_claim_commit`
matches the generation ID, and the explicit path reuses `review_explicit_input` with the combined text
under the newest Turn ID, so it enters the same `_apply`, the same 4,000-character validation and the
same CAS as automatic Review.

Isolation and interaction with the rest of the lifecycle hold. The marker lives on the existing per-user
`_UserState`, so it cannot cross users; `reset` clears it; it is set only after delivery succeeds, so a
confirmation question the user never received cannot arm it; and idle cleanup now keeps the state alive
while a confirmation is outstanding.

## Defect found and fixed: a rejected confirmation was delivered silently

The confirmation guard worked, but the branch that rejects an unbacked confirmation fell through to the
ordinary `review_if_due` path without recording anything. The answer text for that turn was generated by
a model that believed it was confirming a deletion, so the user was told Memory had been erased, nothing
was erased, and `memory_failed` stayed false, leaving the caller unable to notice either. The branch is
reachable exactly on the path this plan documents as safe: after a restart the marker is gone, so the
next affirmative is rejected. The existing test asserted this silence rather than catching it.

Fixed by treating a rejected confirmation as an explicit-Memory failure, so `memory_failed` is set and
the configured notice is appended. Removing that now fails the suite, and the existing test was extended
to assert both the flag and the delivered text.

While fixing it, the notice composition moved ahead of the pre-Compaction Review. The notice shares the
three-item change-summary budget, and a pre-Compaction Review that succeeds with three summaries could
otherwise push the failure notice out, telling the user about unrelated Memory changes while hiding the
failure of the request they just made. A new test pins that the notice survives alongside three
automatic summaries.

## Coverage gap found and fixed: the stale explicit review

Only the raising reviewer was tested. Deleting the check that treats a `STALE` explicit result as a
failure left the whole suite green, even though a lost compare-and-set writes nothing and must reach the
user exactly like a raised failure. A new test returns `STALE` from `review_explicit_input` and asserts
the failure flag, the appended notice, and that no second Review ran. That mutation now fails.

## Instructions for Codex

Nothing further to fix. Confirm main CI succeeds after merge. The later model adapter still owns the
instruction that `delete_all_confirmed` may only be set for an affirmative to the immediately preceding
deletion question; the Orchestrator now checks its own state independently, so a model that sets it
wrongly costs the user a repeated question rather than their Memory.

## Post-review correction: required explicit failure notice

After independently rechecking the final tree, the owner accepted one small contract correction. The
explicit-Memory failure notice is required rather than optional. Without it, a caller that omitted the
configuration could receive `memory_failed=True` only after the Orchestrator had already delivered an
answer claiming the update succeeded. Requiring one non-empty, bounded caller-owned string closes that
normal failure path without a second model call, provider-specific wording, retry, or new state.
Automatic Review failures remain silent as designed.
