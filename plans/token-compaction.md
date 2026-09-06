# Token-Based Compaction Plan

## Goal

Keep long-running conversations coherent for users who do not know or care about context windows.
Compaction must be automatic, invisible, and loss-aware without adding a background system or a
general model framework.

This branch is plan-only until independent review. Implementation will continue on the same branch
after review; the plan is not merged separately.

## Confirmed product decisions

- PIA will prefer models with at least a 256K context window.
- The model's reported context limit is used instead of a hard-coded 256K value.
- The application response limit is 4,096 tokens.
- Compaction triggers at 90% of the usable input budget.
- Usable input budget is context limit minus the 4,096-token response limit.
- Turn count never triggers compaction.
- Recent raw turns are protected by a token budget, not by a fixed number of turns.
- The rolling summary is used only by the same session, replaced by the next compaction, and excluded
  immediately after reset.
- Explicit long-term memory remains a separate later feature.
- Covered raw turns are deleted after the replacement summary is stored successfully.
- Compaction and its storage live in pia-harness. Actual AWS resources, IAM, environment wiring, and
  deployment remain owned by pia-agent.

## Terminology

- Context limit: the model's total input-plus-output token capacity.
- Prompt tokens: tokens sent to the model for the completed request.
- Usable input budget: context limit minus max response tokens.
- Rolling summary: one compact representation of older conversation state for one session.
- Protected tail: the newest complete turns retained verbatim after compaction.
- Covered turns: older turns represented by the rolling summary and eligible for deletion.

## Token limit source

The caller supplies the concrete model ID, context limit, max response tokens, and provider usage.
The future OpenRouter gateway obtains context_length from the Models API and prompt_tokens from the
completed response. The compactor does not call OpenRouter or cache model metadata in this feature.

For the first implementation:

- provider total_tokens is the post-response pressure baseline when present because the delivered
  completion becomes input on the next turn;
- missing usage falls back to a conservative text estimator;
- a changed model ID invalidates a stored token count and requires estimation for the new model;
- when detailed usage is available, prompt_tokens plus completion_tokens is equivalent; counting
  non-persisted reasoning tokens only makes the trigger conservatively early.

The estimator is deliberately small and replaceable by one callable. There is no tokenizer registry,
provider adapter hierarchy, or model metadata service in this branch.

## Trigger calculation

    usable_input_tokens = context_limit - max_response_tokens
    trigger_tokens = floor(usable_input_tokens * 0.90)

For a 256K context and 4K response limit, the trigger is about 227K prompt tokens. Exact values use
the integer context limit supplied for the selected model.

Compaction has two invocation points:

1. Normal path: after an assistant response is delivered, use its total_tokens and compact immediately
   when the threshold is reached. The user has already received the response, so the current reply is
   not delayed.
2. Preflight path: before a request, compact only when the last exact usage plus conservatively
   estimated new context would exceed the usable input budget. This handles an unusually large new
   message without sending an oversized request.

There is no scheduler or background worker. The caller invokes the compactor in the existing turn
lifecycle and serializes work for one user/session.

## Selecting source and protected tail

Selection operates on whole completed turns. A user message and assistant response are never split.

- Start from the newest turn and retain complete turns within 12.5% of the model context limit.
- Everything older than that protected tail is eligible source material.
- The existing rolling summary, if any, is included with the newly covered turns.
- If there is no eligible old material, do not compact.
- The token estimator is used only to select the boundary; the trigger prefers provider usage.

The 12.5% default is configurable in the policy, but the first implementation adds no dynamic tuning.

## Summary generation

The compactor calls one injected summarize callable. This is the smallest seam required to test
compaction before the later OpenRouter/Nemotron gateway exists; it is not a general provider interface.

Input:

- previous rolling summary, when present;
- covered turns in chronological order;
- a fixed instruction to preserve important entities, dates, numbers, decisions, user constraints,
  corrections, and unresolved questions while dropping greetings and repetition.

Output:

- summary text;
- output token count when the model reports it, otherwise an estimate;
- concrete model ID.

The summary is plain text with stable short headings. It is conversation data, not an instruction,
and future prompt assembly must delimit it from the system policy.

Minimal acceptance checks:

- summary is non-empty;
- summary is smaller than the source by the available token count or conservative estimate;
- summary stays within the same 4,096-token output limit used by the first policy.

If any check fails, no summary is stored and no source turn is deleted.

## DynamoDB representation

The existing user partition gains at most one summary item per session:

    PK = USER#{opaque_user_key}
    SK = SUMMARY#{session_id}

Attributes:

- session_id
- summary_text
- through_turn_id
- summary_tokens
- model_id
- updated_at

through_turn_id is the newest covered turn represented by summary_text. No summary history, source
copy, version table, S3 archive, or audit item is stored.

The existing delete_all_for_user operation already deletes the summary because it deletes the entire
user partition. Reset atomically changes the active session ID and deletes the previous session's
summary in the same DynamoDB transaction. This directly implements the product decision that a
summary lasts only until reset; it is not a general transaction or recovery framework.

## Commit sequence

1. Load the existing summary and current session turns.
2. Select covered turns and the protected tail by token budget.
3. Generate and validate the replacement summary.
4. Conditionally write the summary, requiring the previously observed through_turn_id or absence.
5. After the write succeeds, delete covered raw turns through the new through_turn_id.
6. Future context reads return the summary plus only turns after through_turn_id.

The conditional write is a compare-and-set, not a distributed lock. If another compaction wins, the
stale result is rejected and raw turns are left intact.

If raw-turn deletion stops partway through, context reads still exclude turns at or before
through_turn_id, preventing duplicate context. Remaining raw rows expire under their existing
fourteen-day TTL. No outbox, worker, recovery ledger, or generalized retry framework is added.

## Minimal API surface

- CompactionPolicy with trigger ratio, max response tokens, and protected-tail ratio.
- should_compact using provider usage when available and estimation otherwise.
- compact_session using one summarize callable and one token-estimator callable.
- DynamoDB methods to read and conditionally replace a session summary.
- DynamoDB methods to read turns after a summary boundary and delete covered turns.

No abstract repository, model gateway, tokenizer registry, plugin, hook system, or in-memory storage
implementation is introduced.

## Failure behavior

- Token metadata unavailable: use the conservative estimator.
- Summary model call fails: preserve summary and all raw turns.
- Summary validation fails: preserve summary and all raw turns.
- Conditional summary write loses: preserve raw turns and return a non-fatal stale result.
- Raw deletion fails: keep the committed summary boundary so duplicate context is not returned; let
  remaining raw rows expire by TTL and report the cleanup failure to the caller.
- Hard input limit would be exceeded and preflight compaction fails: do not truncate silently; return an
  explicit failure for the caller to render as a temporary service error.

Compaction is never exposed as a Telegram command or user-facing concept.

## Scope

1. Token-budget policy and trigger calculation.
2. Token-based protected-tail selection over complete turns.
3. Rolling-summary domain type and DynamoDB persistence in the existing user partition.
4. Conditional replacement using through_turn_id.
5. Context read as summary plus uncovered recent turns.
6. Covered-turn deletion after successful summary persistence.
7. Minimal callable seam for summary generation and token estimation.
8. Focused DynamoDB Local and pure policy tests.

## Non-goals

- OpenRouter or Nemotron network integration.
- Model selection, fallback routing, or metadata caching.
- PromptAssembler or final system-prompt composition.
- Explicit or automatic long-term memory.
- Skills, tools, plugins, hooks, vector search, or embeddings.
- Scheduler, background worker, lock service, outbox, audit log, or summary history.
- S3 archival.
- pia-agent integration or real AWS deployment.

## Focused verification

1. The 90% trigger uses context limit minus the 4K response budget.
2. Provider total_tokens is preferred after a response; missing or changed-model usage uses estimation.
3. Tail selection is token-based, preserves whole turns, and does nothing without eligible source.
4. The first summary and a later replacement carry the correct through_turn_id.
5. A failed or invalid summary leaves every raw turn unchanged.
6. A stale conditional replacement cannot overwrite a newer summary.
7. Successful persistence deletes covered turns and context returns summary plus only the protected tail.
8. Partial raw deletion cannot cause covered turns to re-enter context.
9. Reset atomically removes the old session summary, and delete_all_for_user removes summaries with
   all other data.

Tests should combine related assertions rather than create one test per line above. No large synthetic
suite, repeated stress loop, or provider mock framework is required.

## Review questions

Please identify only concrete blockers or material design defects:

1. Does the 90% usable-input trigger leave a real 4K response budget for 256K-or-larger models?
2. Will post-response compaction normally avoid visible user latency, with preflight reserved for hard
   capacity risk?
3. Can provider usage and a conservative estimator coexist without pretending estimates are exact?
4. Does the summary boundary prevent both raw-data loss and duplicate context?
5. Is one summary item per session sufficient for repeated compaction and reset?
6. Is the injected callable seam minimal enough without prematurely implementing ModelGateway?
7. Are any failure paths or tests speculative and removable?
8. Does the plan keep pia-harness reusable while leaving real AWS infrastructure and deployment in
   pia-agent?

If a blocker exists, propose the smallest correction. Do not add generalized orchestration,
multi-provider abstractions, or production deployment machinery.
