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
- The rolling summary is used only by the same session, replaced by the next compaction, and deleted
  after reset or account closure.
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

The fallback estimator counts UTF-8 bytes rather than model tokens. This is intentionally
conservative and makes the protected Korean tail smaller than 12.5% until a model tokenizer is
connected; it must not be compared directly with a provider token-output limit.

## Trigger calculation

    usable_input_tokens = context_limit - max_response_tokens
    trigger_tokens = floor(usable_input_tokens * 0.90)

For a 256K context and 4K response limit, the trigger is about 227K prompt tokens. Exact values use
the integer context limit supplied for the selected model.

After an assistant response is delivered, use its total_tokens and compact immediately when the
threshold is reached. The user has already received the response, so the current reply is not delayed.
Preflight compaction is deferred until a future input channel can submit documents or another payload
large enough to exceed the remaining headroom.

There is no scheduler or background worker. The caller invokes the compactor in the existing turn
lifecycle and serializes work for one user/session.

## Selecting source and protected tail

Selection operates on whole completed turns. A user message and assistant response are never split.

- Start from the newest turn and retain complete turns within 12.5% of the model context limit.
- Always retain the newest completed turn verbatim, even when it alone exceeds the tail budget.
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
user partition. Reset keeps the existing conditional pointer update and then deletes the previous
session's summary. Once the pointer moves, the old summary is unreachable even if that cleanup fails;
account deletion still removes every item in the partition.

An active session summary has no TTL. It remains available even when all raw turns have expired, so a
user returning after a long break keeps the compacted context. It is removed by reset or account
closure, not by speculative inactivity detection.

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
- One boundary-aware load_context method that returns the summary and only turns after its boundary;
  it replaces list_turns as the public context-read path.
- DynamoDB methods to delete covered turns.

No abstract repository, model gateway, tokenizer registry, plugin, hook system, or in-memory storage
implementation is introduced.

## Failure behavior

- Token metadata unavailable: use the conservative estimator.
- Summary model call fails: preserve summary and all raw turns.
- Summary validation fails: preserve summary and all raw turns.
- Conditional summary write loses: preserve raw turns and return a non-fatal stale result.
- Raw deletion fails: keep the committed summary boundary so duplicate context is not returned; let
  remaining raw rows expire by TTL and report the cleanup failure to the caller.

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
2. Will post-response compaction normally avoid visible latency for the user who triggered it?
3. Can provider usage and a conservative estimator coexist without pretending estimates are exact?
4. Does the summary boundary prevent both raw-data loss and duplicate context?
5. Is one summary item per session sufficient for repeated compaction and reset?
6. Is the injected callable seam minimal enough without prematurely implementing ModelGateway?
7. Are any failure paths or tests speculative and removable?
8. Does the plan keep pia-harness reusable while leaving real AWS infrastructure and deployment in
   pia-agent?

If a blocker exists, propose the smallest correction. Do not add generalized orchestration,
multi-provider abstractions, or production deployment machinery.

## Review record: 2026-09-06, Claude, plan commit 159136d

Plan-only review. Nothing was implemented and nothing was merged. The design is sound and the
arithmetic holds; three gaps and two pieces of unnecessary prevention are listed below, each with the
smallest correction.

Checked arithmetic first. For a 256K context and a 4,096-token response limit the usable input budget
is 258,048 tokens and the trigger is 232,243, which leaves roughly 26K of headroom above the reserved
response space. After a compaction the next prompt is the summary of at most 4,096 tokens plus a
protected tail of 12.5% of context, about 37K in total, so the trigger cannot fire again immediately
and compaction cannot oscillate. At trigger time the material handed to the summarizer is about 200K
tokens, which still fits a 256K model. Preferring provider total_tokens as the next turn's baseline is
correct because the delivered completion becomes input, and counting reasoning tokens only makes the
trigger fire early rather than late.

The commit sequence is right in the order that matters: the summary is written under a compare-and-set
on the previously observed through_turn_id, and only then are covered turns deleted. A partial deletion
cannot resurrect covered context because reads are bounded by through_turn_id, and the leftover rows
expire under the existing fourteen-day TTL. The compare-and-set is at the minimum necessary level: one
conditional write, a losing writer aborting without touching raw turns, and no lock, lease or ledger.
Session-scoped keys make reset and account closure work by construction, since SUMMARY#{session_id} is
unreachable after the pointer moves and delete_all_for_user removes the whole partition.

Sortable turn IDs from the previous feature carry this design: "turns after through_turn_id" is a
plain sort-key range on the existing schema, with no index and no scan.

## Gaps to close before implementing

1. The newest turn can be summarized away. Tail selection retains complete turns within 12.5% of
   context, but nothing says what happens when the newest turn alone exceeds that budget. As written
   the tail can be empty, the user's most recent exchange becomes covered material, and the next reply
   loses the thing the user just said. This is the exact failure the feature exists to prevent.
   Smallest correction, in "Selecting source and protected tail":
   `The newest completed turn is always retained verbatim, even when it alone exceeds the tail budget.`

2. Summary retention is undefined for an abandoned session. Raw turns expire in fourteen days; the
   summary, which is derived from the same content, would live forever unless the user resets or closes
   the account. Do not fix this with a plain TTL on the summary item: a user who chats daily may compact
   less often than every fourteen days, and the summary would expire underneath an active conversation,
   silently truncating context. Decide explicitly, and record the decision here. The smallest safe
   option is to keep the summary untimed like the pointer and delete it lazily on the read path:
   `get_or_create_active_session deletes a summary whose session has no unexpired turns left.`

3. Two read APIs would coexist. list_turns returns every unexpired turn of a session, while this plan
   adds a boundary-aware read. Between the summary write and the end of covered-turn deletion the two
   disagree, and a caller that picks the wrong one sends duplicate context. Say which one callers use.
   Smallest correction, in "Minimal API surface":
   `The boundary-aware read replaces list_turns for context assembly; list_turns keeps its meaning only
   for tests and administrative inspection.`

## Prevention that can be removed

4. Reset does not need a DynamoDB transaction. The summary key contains the session ID, so once the
   pointer moves no read can reach the old summary, and delete_all_for_user still removes it at account
   closure. A transaction buys nothing here and costs a TransactWriteItems path plus
   TransactionCanceledException handling, which would be the most intricate error handling in the
   store. Keep the existing conditional UpdateItem on the pointer and delete the old summary after it;
   a failed delete leaves a row that nothing reads and that gap 2's rule cleans up.

5. The preflight path cannot fire under the only caller that exists. Telegram messages are capped well
   below the roughly 26K of headroom the trigger leaves, so no single new message can overflow the
   budget. Keep it only if it stays a few lines inside the same entry point as the post-response check,
   with one test rather than a second documented path. Revisit it when a caller can submit a large
   input such as an uploaded document.

## Answers to the review questions

1. Yes. The response budget is subtracted before the 90% is applied, so the reserved 4,096 tokens are
   never spent by input.
2. Yes for the user whose turn triggered it, because the reply is already delivered. One integration
   note for pia-agent: the Bot processes updates sequentially in one loop, so a summarization call
   placed inline would stall other users' messages. That belongs in the pia-agent integration, not
   here, but the plan should not assume the call is free.
3. Yes. Provider usage is preferred, estimation is the fallback, and a changed model ID invalidates a
   stored count.
4. Yes, with gap 1 closed.
5. Yes. One item per session is enough because replacement advances through_turn_id in place.
6. Yes. One summarize callable and one estimator callable are the smallest seam that lets the feature
   be tested before the gateway exists.
7. See item 5. Everything else in the failure list corresponds to a reachable state.
8. Yes. Keeping AWS resources and deployment in pia-agent is the right split and is already reflected
   in the branch.

## Instructions for Codex

Apply corrections 1, 2 and 3 to the plan text before writing code, and record the decision made for 2.
Remove the transaction sentence from the DynamoDB representation section per item 4. Decide item 5 and
say which way you went. None of this changes the architecture; if all five are handled, the plan is
ready to implement as written.

## Owner decisions after review

1. The newest completed turn is always retained verbatim.
2. A summary stays untimed for the active session, even when raw turns expire, because preserving
   context after a long break is a primary user experience requirement. Reset and account closure
   remove it.
3. load_context is the only public context-read API and replaces list_turns.
4. Reset uses the existing conditional pointer update followed by summary deletion; no DynamoDB
   transaction is added.
5. Preflight compaction is excluded until a future channel can submit large documents. Telegram-only
   input cannot consume the headroom left by the 90% trigger.
6. The fallback byte estimator may conservatively shrink the protected tail for Korean text. Output
   limits are enforced only when the summarizer reports a real token count; the same estimator still
   verifies that a summary is smaller than its source.

## Review record: 2026-09-06, Claude, implementation commit fc0d242

Implementation review against this plan. Nothing was implemented by the reviewer and nothing was
merged. Verdict: one blocker, fixable in one line. Everything else in the feature is correct and
minimal, and the branch is ready to merge once that line changes.

All five owner decisions are in the code. The newest turn is retained unconditionally in _split_turns,
the summary has no TTL, load_context replaces list_turns as the only context read, reset keeps its
conditional pointer update and then deletes the old summary, and preflight is absent.

Verified by running, not only by reading. Twelve tests pass against DynamoDB Local and ruff reports no
unused imports or definitions. Two paths the suite does not cover were probed directly.

Repeated compaction was run end to end. After two compactions the partition held exactly one summary
row, the boundary advanced, the second summarize call received the previous summary and only the turns
not already covered, and load_context returned the protected tail with no duplicate and no missing
turn. Account closure was probed with a summary present: delete_all_for_user removed the pointer, the
turn and the summary, leaving the partition empty.

The write order gives no path that loses raw data. Covered turns are deleted only after
replace_summary returns True, a lost compare-and-set returns None without deleting, a failed or
invalid summary raises before any write, and a deletion that stops partway leaves rows that
load_context already excludes by boundary and that expire under the existing TTL.

## Blocker: the byte estimator is compared against a token limit

conservative_token_estimate counts UTF-8 bytes, and compact_after_response compares that number to
policy.max_response_tokens. Korean text is three bytes per character, so a normal rolling summary is
rejected for being too long when the summarize callable reports no token count.

Probed with the real policy: a 1,500-character Korean summary estimates 4,500 against a 4,096 limit and
raises "summary exceeds the output token limit". The identical summary is stored when a provider token
count is supplied. Until the OpenRouter gateway reports usage, that is the normal path, so compaction
would fail on every attempt for a Korean conversation, raw turns would never be compacted, and the
prompt would keep growing until requests exceed the model limit. The user sees a conversation that
breaks, with nothing in the failure naming the cause.

Smallest correction, in compact_after_response: apply the output-limit check only to a
provider-reported count.

    if output.token_count is not None and summary_tokens > self._policy.max_response_tokens:
        raise SummaryValidationError("summary exceeds the output token limit")

The model's own max output setting already bounds generation, and the "summary must be smaller than
its source" check still guards the estimated path. Do not fix this by scaling bytes into tokens with a
fudge factor.

## Non-blocking notes

1. The same unit mismatch makes the protected tail smaller than the plan states for non-ASCII text. The
   tail budget is 12.5% of the context in tokens, but turn cost is measured in bytes, so a Korean tail
   holds roughly a third of the intended conversation. This errs toward summarizing more rather than
   losing data, so it is not a blocker, but the plan should say the default estimator is a byte count
   and that a real tokenizer will change the effective tail size.
2. Repeated compaction has no test. It works, as probed above, but nothing pins it, and it is the path
   where a regression would silently drop turns. One test that compacts twice and asserts a single
   summary row, an advanced boundary and no duplicate turn would cover it.
3. Recorded consequence of owner decision 2: a summary of an abandoned session is retained until the
   user resets or closes the account, outliving the fourteen-day raw-turn retention. That was chosen
   deliberately for context after a long break; it is written here so the retention story stays visible.

## Instructions for Codex

Change the one line above, add the repeated-compaction test from note 2, and add the sentence from note
1 to the plan. Then the branch can merge. Nothing else needs to change; the API surface, the failure
paths and the test set are the right size, and no speculative structure was added.
