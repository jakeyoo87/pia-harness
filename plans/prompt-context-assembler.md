# Prompt and Context Assembler Plan

## Goal

Add one provider-independent, side-effect-free component that turns the conversation objects already
produced by `pia-harness` into an ordered, budget-checked model context. It defines what the later model
adapter receives, but does not call a model, read DynamoDB, compact data, retry, or talk to a user.

This branch is plan-only until independent review. The current handoff assigns plan authorship to
Codex and plan review to Claude. Later role assignments may change by explicit user instruction, while
the same feature branch remains in use.

## Existing baseline

`main` already provides:

- `MemoryDocument`, one bounded long-term Memory document per opaque user;
- `ConversationContext`, containing an optional rolling Summary and only the raw Turns after its
  `through_turn_id` boundary;
- `DynamoDBConversationStore.get_memory` and `load_context` for the later caller to load those objects;
- token-based Compaction, which owns selection, summarization, persistence, and deletion of old Turns;
- a 4,096-token default response reserve in `CompactionPolicy`.

The assembler consumes these existing domain objects. It does not replace their storage or boundary
logic.

## Responsibility boundary

The assembler owns only:

1. validating that supplied user, session, Memory, Summary, and Turn identities agree;
2. producing context parts in one deterministic order;
3. marking the system prompt as trusted and all stored or current conversation content as untrusted;
4. calculating the available input budget from caller-supplied limits and reserves;
5. asking one injected counter for the complete assembled input size;
6. returning the assembled context when it fits or a structured overflow error when it does not.

The assembler does not:

- remove, shorten, summarize, rank, or rewrite any content;
- invoke `TokenCompactor` or decide whether Compaction is required;
- retry assembly after Compaction;
- generate a user-facing length warning;
- read or write DynamoDB;
- render OpenRouter-, Nemotron-, or other provider-specific request JSON;
- include tools, Portfolio data, market data, or order instructions;
- implement Memory Review, natural-language Memory commands, or response delivery.

If assembly overflows, the later Conversation Orchestrator may ask the Compactor to reduce stored
conversation context and then call the assembler again. That trigger and retry belong to the later
Compaction/Orchestrator integration, not this feature.

## Input contract

`PromptContextAssembler.assemble` receives:

- `user_key`: the opaque user whose context is being assembled;
- `session_id`: the active conversation session;
- `system_prompt`: non-empty trusted instructions supplied by the later PIA integration;
- `memory`: `MemoryDocument | None`;
- `conversation`: one `ConversationContext` previously loaded through `load_context`;
- `current_user_message`: the non-empty current request, not yet stored as a completed Turn;
- `context_limit`: the model's total context window in tokens;
- `reserved_response_tokens`: caller-configurable, defaulting to 4,096;
- `reserved_input_tokens`: non-negative space reserved by the later adapter for tool schemas or
  provider framing that is not represented as a context part;
- one injected `count_input_tokens` callable.

The assembler is deliberately pure. Loading `memory` and `conversation` together, choosing the active
session, and coordinating concurrent requests remain caller responsibilities.

## Structured output

The assembler returns an `AssembledPromptContext` containing:

- an ordered tuple of `PromptContextPart` values;
- `estimated_input_tokens` returned by the injected counter;
- `input_budget` after all reserves;
- the original `context_limit`, `reserved_response_tokens`, and `reserved_input_tokens` for later
  diagnostics.

Each `PromptContextPart` contains:

- a `kind` from `SYSTEM`, `MEMORY`, `SUMMARY`, `USER_TURN`, `ASSISTANT_TURN`, or `CURRENT_USER`;
- its exact text content;
- a trust classification of `TRUSTED_INSTRUCTION` or `UNTRUSTED_DATA`.

This is not yet a provider message list. A later adapter maps these typed parts to the provider's
supported roles and wire format without promoting an untrusted part to trusted instructions.

## Assembly order

Parts are emitted exactly in this order:

1. one `SYSTEM` part;
2. `MEMORY` when a non-empty Memory document exists;
3. `SUMMARY` when a Conversation Summary exists;
4. each recent completed Turn in chronological order, expanded as one `USER_TURN` followed by one
   `ASSISTANT_TURN`;
5. one `CURRENT_USER` part.

Empty Memory is omitted. Missing Summary and an empty recent-Turn tuple are valid. System prompt and
current user message are required and are never silently dropped or truncated.

Only `SYSTEM` is `TRUSTED_INSTRUCTION`. Memory, Summary, historical user and assistant messages, and
the current user message are all `UNTRUSTED_DATA`. This classification is structural metadata; the
assembler does not rely on XML tags or other delimiters that untrusted text could imitate.

## Identity and boundary validation

The assembler rejects inconsistent caller input rather than silently repairing it:

- `memory.user_key`, when Memory exists, must equal `user_key`;
- Summary user and session must equal `user_key` and `session_id`;
- every Turn user and session must equal `user_key` and `session_id`;
- Turn IDs must be strictly increasing in the supplied order;
- when Summary exists, every supplied Turn ID must be greater than `summary.through_turn_id`.

The final check prevents Summary/Turn duplication even if a caller bypasses `load_context`. It does not
repeat storage filtering or mutate the supplied context.

## Token budget contract

The available input budget is:

    input_budget = context_limit - reserved_response_tokens - reserved_input_tokens

All values are token counts. `context_limit` and `reserved_response_tokens` must be positive;
`reserved_input_tokens` must be non-negative; and the resulting `input_budget` must be positive.

The injected counter receives the complete ordered tuple of parts, including the system prompt and all
untrusted content, and returns the input token count including any canonical per-part overhead it owns.
The later provider adapter can inject an exact model-aware counter. Tests use a deterministic counter;
the assembler does not guess a provider tokenizer.

If the counter returns a negative or non-integer value, assembly fails validation. If the returned
count is equal to the input budget, assembly succeeds. If it is greater, the assembler raises
`ContextBudgetExceeded` with:

- `required_input_tokens`;
- `input_budget`;
- `context_limit`;
- both reserve values.

The overflow error does not contain or log prompt text. No part is removed and no Compaction or retry
is attempted.

## Minimal API surface

- `PromptContextKind` and `PromptTrust` enums;
- `PromptContextPart`;
- `AssembledPromptContext`;
- `PromptContextValidationError`;
- `ContextBudgetExceeded`;
- `PromptContextAssembler` with one injected token-count callable and one `assemble` method.

No storage adapter, provider abstraction, prompt-template framework, relevance selector, tool registry,
or orchestration base class is added.

## Failure behavior

- Empty trusted system prompt or current user message: validation error.
- User/session mismatch, unsorted Turns, or Summary overlap: validation error.
- Invalid limits, reserves, or counter result: validation error.
- Input count above budget: `ContextBudgetExceeded` with counts only.
- Counter failure: propagate before returning an assembled result.

All failures are side-effect free. User messaging, Compaction, retry, and provider fallback are left to
later layers.

## Scope

1. Pure context domain types and assembler.
2. Exact ordering, trust, identity, and Summary-boundary validation.
3. Model-agnostic budget and structured overflow contracts.
4. Focused unit tests without DynamoDB or network access.
5. README and this plan update.

## Non-goals

- DynamoDB changes or migrations.
- Changes to Session, Memory, Summary, or Compaction persistence.
- Forced pre-request Compaction or retry orchestration.
- Actual tokenizers, model clients, OpenRouter, or Nemotron.
- Natural-language Memory intent detection or Memory Review scheduling.
- User-facing notices or Telegram behavior.
- `pia-agent`, AWS, IAM, Secrets Manager, infrastructure, or deployment changes.

## Focused verification

1. Full input emits exact section order and chronological user/assistant Turn pairs.
2. Missing or empty optional Memory, Summary, and recent Turns are omitted correctly.
3. System is the only trusted part; every stored and current conversation part is untrusted.
4. User/session mismatch, unsorted Turn IDs, and Summary-boundary overlap are rejected.
5. Token budget arithmetic validates limits and both reserves.
6. Exact-budget input succeeds; one-token overflow raises counts-only `ContextBudgetExceeded` without
   truncating parts.
7. Invalid counter results and counter exceptions fail without an assembled result.
8. Existing Session, Compaction, and Memory tests remain unchanged and pass in the full suite.

## Questions for independent review

Please identify only concrete blockers, material omissions, or unnecessary scope:

1. Is a typed provider-independent part list the right boundary, or does it defer too much required
   role/rendering behavior to the later adapter?
2. Is the trust classification sufficient to keep Memory, Summary, and historical assistant output
   from becoming instructions when the provider adapter is added?
3. Should the assembler validate identity, ordering, and Summary boundaries defensively, or trust only
   `load_context` output?
4. Does the injected whole-context token counter plus `reserved_input_tokens` account cleanly for
   later provider and tool-schema overhead without binding this feature to a model?
5. Is returning overflow without Compaction, retry, or user messaging the correct responsibility
   boundary?
6. Is any proposed type, field, validation, or test unnecessary for this first assembler?

If a blocker exists, propose the smallest correction. Do not add a provider adapter, model call,
Compaction trigger, retry loop, tool schema, Portfolio context, user-facing behavior, or AWS work.

## Review record: 2026-09-09, Claude, plan commit a38323d

Plan-only review against the merged Session, Compaction, and Memory features. Nothing was implemented
and nothing was merged. One blocker, three smaller corrections, and one removal follow. The shape of
the feature is right and most of it needs no change.

What holds. The typed part list is the correct boundary: it is the only artifact in this repository
that can carry a trust classification, because a provider message list has nothing but roles. The
domain objects line up exactly with what `main` already stores, including the two details that are easy
to get wrong — Memory carries no session, so only its `user_key` can be checked, and a Memory document
is allowed to be empty with an advanced boundary, which is why omitting empty Memory rather than
emitting a blank part is correct. Re-checking the Summary boundary is worth its ten lines: it is the
last place before content reaches a model where a cross-user or duplicated-context bug can still be
caught, and `load_context` applying the same filter does not make a second check redundant when the
consequence of a caller mistake is another user's conversation in the prompt. Not validating
`memory.last_reviewed_turn_id` is right; assembly does not depend on it. The chosen order also puts the
stable content first, so a later provider adapter can reuse a cached prefix across turns, and Memory
sits where it changes least often.

## Blocker: reserved_input_tokens can open a band where assembly always fails and Compaction always declines

`TokenCompactor` exposes only `compact_after_response`, which begins by asking
`CompactionPolicy.should_compact` and returns `None` when the trigger has not been reached. There is no
force-compaction entry point. So the Orchestrator described in the responsibility boundary can ask for
Compaction, but it cannot make it happen below the trigger.

The two components measure a different budget:

    assembler  input_budget = context_limit - reserved_response_tokens - reserved_input_tokens
    compaction trigger      = 0.90 * (context_limit - max_response_tokens)

With the defaults and `reserved_input_tokens` of zero, the trigger sits at ninety percent of the
assembler's budget and Compaction always fires first, which is the intended behavior. But
`reserved_input_tokens` is caller-configurable and is exactly the field that grows as PIA adds tool
schemas. Once it exceeds the Compaction headroom, the trigger rises above the assembler's budget and a
band opens between them. A conversation whose next request lands in that band overflows on every
attempt while `should_compact` reports that no Compaction is due — including when the Orchestrator
passes the overflow's own `required_input_tokens` as the estimate, because that value is still below
the trigger. Nothing in either feature recovers, and the user's conversation stops working
permanently. For a 262,144-token model the band opens once `reserved_input_tokens` passes about 25,800.

Smallest correction: state the invariant in the token budget contract and leave the assembler pure.

    reserved_input_tokens must not exceed the Compaction headroom:
    reserved_input_tokens <= (1 - CompactionPolicy.trigger_ratio) * (context_limit - reserved_response_tokens),
    with CompactionPolicy.max_response_tokens configured equal to reserved_response_tokens. The later
    Orchestrator asserts this once at startup; the assembler does not import Compaction to check it.

Add one verification item that the arithmetic holds for the default policy, so the relationship is
pinned by a test rather than by a paragraph.

## Smaller corrections

1. Name the two rendering rules the typed parts exist to enforce. The plan tells the adapter not to
   promote untrusted parts to trusted instructions, but `MEMORY` and `SUMMARY` have no natural provider
   role, and the two most likely implementations both damage the property: folding them into the system
   message promotes them, and emitting them as a bare user message makes stored content
   indistinguishable from what the user just typed. State both rules here, where the reason for the
   trust field lives, so the adapter review has something to check:

       No UNTRUSTED_DATA part may be rendered into a provider system or developer role. An adapter may
       label untrusted parts when rendering them; the label is a hint to the model, and the boundary is
       the role separation plus the guarantee that the system part never contains stored content.

2. Name the Orchestrator's termination condition. Overflow plus retry after Compaction is the right
   split, but Compaction returns `None` when every remaining Turn is inside the protected tail, and a
   retry loop that does not treat that as a stop condition never ends. One sentence in the
   responsibility boundary: the Orchestrator stops and surfaces the failure when Compaction reports no
   progress, rather than reassembling.

3. Say that the counter must come from the adapter that renders the request. The split between a
   whole-context counter and `reserved_input_tokens` is clean, but only if the counter measures the same
   text the adapter will send. If they diverge, the budget check is arithmetic about a string nobody
   transmits.

## Removal

4. `AssembledPromptContext` echoes `context_limit`, `reserved_response_tokens`, and
   `reserved_input_tokens` back to a caller that just supplied them and is still holding them. Keep
   `input_budget` and `estimated_input_tokens`, which are derived, and keep all of the reserves on
   `ContextBudgetExceeded`, where they travel up the stack away from the call site and are genuinely
   needed. Dropping the three echoed fields on the success path matches the precedent set when
   `reviewer_model_id` was removed from the Memory item for having no reader.

## Answers to the review questions

1. Right boundary. The typed list is the only place trust can be represented, and it defers rendering
   without deferring the rule; correction 1 makes that rule explicit.
2. Sufficient in this repository, not yet sufficient at the adapter. See correction 1.
3. Validate defensively. The checks are cheap and guard the one failure that is both silent and
   catastrophic.
4. Yes, once the invariant from the blocker is written down and the counter is sourced per correction 3.
5. Yes. Returning counts and refusing to truncate is the correct boundary, and it is what makes the
   overflow recoverable by a layer that can see the whole turn.
6. Only the three echoed fields in removal 4. Every type, enum value, and validation earns its place.

## Instructions for Codex

Add the budget invariant and its verification item, the two rendering rules, the Orchestrator
termination sentence, and the counter-sourcing sentence; drop the three echoed fields. All five are
plan text plus one test. With them in place the plan is ready to implement as written, and no provider
adapter, Compaction trigger, retry loop, or tool schema should appear in this branch.
