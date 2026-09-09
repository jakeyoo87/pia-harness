# Explicit Memory Current-Input Plan

## Goal

Allow a caller that has already identified an explicit remember, correct, or targeted-forget request
to review that current user input before it becomes a persisted completed Turn. This closes the gap
between the existing promise of immediate explicit Memory updates and the current reviewer, which can
read only previously persisted completed Turns.

This branch is plan-only until independent review. The current handoff assigns plan authorship to
Codex and plan review to Claude. Implementation continues on this same branch after review; later role
assignments may change by explicit user instruction.

## Existing baseline

`main` already provides:

- a completed Turn contract: one accepted user message plus a final assistant response that was
  successfully delivered;
- `new_turn_id(created_at)`, generated when a message is accepted and reused for persistence retries;
- `AutomaticMemoryReviewer.review_if_due`, which reviews earlier persisted Turns after a revisit;
- `force_review`, used for persisted pre-Compaction and pre-reset flushes;
- `MemoryReviewRequest`, currently containing only persisted completed Turns;
- a Memory boundary CAS through `last_reviewed_turn_id`;
- `allow_clear`, which permits CLEAR only for an explicit targeted forget;
- change-summary output for the later caller to include in the same user-visible answer.

The missing case is the current explicit request. Before its final assistant response is delivered it
is not a completed Turn, so existing `force_review` cannot see it. Waiting until delivery and Turn
persistence would make immediate Memory confirmation unavailable for that same answer.

## Product boundary

This feature adds a storage-safe current-input seam. It does not determine whether natural language is
a Memory command.

The later caller is responsible for detecting explicit intent and choosing one of:

- remember or correct: explicit review with CLEAR prohibited;
- targeted forget: explicit review with CLEAR allowed;
- forget everything: confirmed direct `delete_memory`, not an LLM Review.

The accepted current input and every persisted Turn are untrusted conversation data. They cannot
modify system policy, Portfolio facts, Risk Check, or order approval.

## Current input contract

Add a bounded `CurrentMemoryInput` domain value containing:

- `user_key`;
- `session_id`;
- `turn_id` generated once by `new_turn_id` when the current message was accepted;
- exact `user_message`;
- timezone-aware `created_at` matching the timestamp prefix in `turn_id`.

It contains no assistant response and is not written as a Turn by this feature. The later caller uses
the same turn ID, user message, and created_at if it eventually appends the completed Turn after
successful delivery.

Turn-ID format and timestamp matching are the same invariant for current and completed inputs. Extract
the existing validation into one small session-level helper used by both paths rather than maintaining
two regular expressions or allowing an invalid boundary to reach the reviewer.

## Reviewer input

Extend `MemoryReviewRequest` with:

    current_input: CurrentMemoryInput | None = None

Automatic revisit, pre-Compaction, and pre-reset Reviews pass `None`. The explicit current-input path
passes exactly one accepted current input, kept structurally separate from prior completed Turns.

The reviewer receives:

- current Memory text;
- non-expired persisted completed Turns after the current Memory boundary;
- the accepted current input;
- the existing fixed instruction and 4,000-character limit;
- `allow_clear`, true only for targeted forget.

The fixed instruction continues to treat every Turn and current input as data, not instructions. It
does not treat the absence of an assistant response as permission to infer additional user intent.

## API change

Add:

    review_explicit_input(
        *,
        user_key: str,
        session_id: str,
        current_input: CurrentMemoryInput,
        allow_clear: bool = False,
        now: datetime | None = None,
    ) -> MemoryReviewResult | None

Tighten the generic persisted-Turn method to:

    force_review(
        *,
        user_key: str,
        session_id: str,
        now: datetime | None = None,
    ) -> MemoryReviewResult | None

Generic force Review never allows CLEAR. Only `review_explicit_input` exposes `allow_clear`, so a
pre-Compaction or pre-reset caller cannot accidentally opt into document deletion. No compatibility
shim is needed because `pia-harness` has not yet been integrated with `pia-agent`.

## Validation and ordering

Before calling the injected reviewer:

1. validate the current input fields, turn-ID format, and created_at match;
2. load the current Memory and its boundary;
3. load non-expired persisted Turns after that boundary from the same user/session;
4. reject a current input whose identity differs from the method's user/session value;
5. require `current_input.turn_id` to be greater than every included persisted Turn ID;
6. if a Memory boundary exists, require the current turn ID to be greater than it.

If the current turn ID equals the Memory boundary, return `None` without calling the reviewer: the
same accepted input was already processed. If it is lower than the boundary, reject it as stale rather
than applying an older request to newer Memory.

The successful replacement boundary is always `current_input.turn_id`, including UNCHANGED and CLEAR.
The existing conditional Memory write still requires the previously observed boundary or item
absence, so a concurrent winner cannot be overwritten.

## Lifecycle and delivery semantics

The later caller's intended order is:

    accept current user message and assign turn_id
    -> detect explicit Memory intent
    -> review_explicit_input
    -> create the final answer and append any successful change summary
    -> deliver the final answer
    -> append the completed Turn with the same turn_id after delivery succeeds

The Memory write intentionally precedes answer delivery. An explicit Memory request is a user-directed
state change, so a delivery failure does not roll it back. Rolling it back could also overwrite a
concurrent Memory winner. If delivery later succeeds, the completed Turn is appended under the same
boundary ID. If delivery or Turn persistence never succeeds, the boundary may reference an accepted
but absent completed Turn; later IDs still sort after it, so subsequent boundary queries skip no stored
Turn.

The caller must not claim a Memory update unless the Review write succeeded. Reviewer failure, invalid
output, and lost CAS return or raise before a successful change summary is available. User-facing
wording and delivery remain outside the harness.

## Interaction with interruption

The next Conversation Orchestrator may combine multiple user messages received while one answer is in
flight. That future component must provide one final accepted current input and one boundary turn ID
for the combined request before calling this API. This feature does not implement pending-message
aggregation, generation cancellation, or superseded-response handling.

If an explicit Memory Review has already committed before a later message interrupts generation, the
Memory change remains valid because it came from an accepted explicit user request. The Orchestrator
must carry its successful change summary into the replacement final response rather than repeating the
Review.

## Persistence and failure behavior

- No new DynamoDB item or attribute is added.
- REPLACE, UNCHANGED, CLEAR, change-summary validation, and boundary CAS remain unchanged.
- Reviewer failure or invalid output leaves Memory and boundary unchanged.
- Lost CAS returns STALE with no change summary; the caller may reload and retry at most through its
  own request lifecycle.
- Equal-boundary replay returns `None` without another reviewer call.
- Stale or out-of-order current input is rejected before reviewer invocation.
- Completed-Turn delivery and append failures do not roll Memory back.
- Account closure and direct complete Memory deletion remain unchanged.

## Minimal API surface

- `CurrentMemoryInput`;
- one shared accepted-turn-ID validation helper;
- optional `current_input` on `MemoryReviewRequest`;
- `AutomaticMemoryReviewer.review_explicit_input`;
- removal of `allow_clear` from generic `force_review`.

No intent enum, command parser, provider abstraction, pending queue, delivery callback, transaction,
outbox, rollback record, Memory history, or Orchestrator base class is added.

## Scope

1. Current-input domain and shared validation.
2. Explicit current-input Review and current-ID boundary advancement.
3. CLEAR reachable only through the targeted-forget current-input path.
4. Idempotent equal-boundary and stale-input behavior.
5. Focused pure-policy and DynamoDB Local tests.
6. README and this plan update.

## Non-goals

- Natural-language remember/correct/forget detection.
- Actual model, OpenRouter, or Nemotron calls.
- Answer generation, notification rendering, or delivery.
- Interruption, pending-message aggregation, or per-user generation coordination.
- Compaction or reset orchestration.
- `pia-agent`, Telegram, AWS infrastructure, or deployment changes.

## Focused verification

1. An explicit current input with no earlier completed Turn can create Memory and advance the boundary
   to its preassigned turn ID.
2. Earlier non-expired unreviewed completed Turns and the structurally separate current input reach the
   reviewer once and in order.
3. The completed Turn later appended under that same ID is not reviewed again; only newer Turns remain
   eligible.
4. Remember/correct, revisit, Compaction, and reset reject CLEAR; targeted forget can allow it.
5. Equal-boundary replay skips the reviewer; lower-boundary input is rejected before the reviewer.
6. Invalid current identity, ID format, timestamp, or ordering is rejected before the reviewer.
7. Reviewer failure, invalid output, and lost CAS preserve Memory and boundary.
8. Successful change summary is returned only after the conditional Memory write succeeds.
9. Existing automatic revisit, forced persisted Review, reset, deletion, and full Harness tests remain
   green.

## Questions for independent review

Please identify only concrete blockers, material omissions, or unnecessary scope:

1. Is advancing `last_reviewed_turn_id` to an accepted current input before its completed Turn exists a
   safe boundary, including permanent delivery or append failure?
2. Is keeping a successful explicit Memory update when response delivery fails the correct user-action
   precedence, or does it create a misleading state that needs a different commit point?
3. Does separating `current_input` from persisted completed Turns give the later reviewer adapter enough
   information without weakening the completed-Turn contract?
4. Should equal-boundary replay return `None`, or does the caller need an explicit already-processed
   result despite change-summary history being intentionally absent?
5. Is removing `allow_clear` from generic `force_review` the smallest reliable way to make targeted
   current-input forget the only LLM CLEAR path?
6. Do interruption, process failure, or delivery failure create a case where a committed explicit
   change summary cannot safely be carried into the replacement response, and is best-effort notice an
   acceptable boundary without durable notification state?
7. Are any proposed types, validations, failure rules, or tests unnecessary?

If a blocker exists, propose the smallest correction. Do not add intent parsing, model networking,
delivery, interruption, transactions, outbox state, Memory history, `pia-agent`, or AWS work.
