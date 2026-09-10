# OpenRouter Model Adapter Plan

## Goal

Complete the reusable `pia-harness` model boundary so the library can call a configured model
through OpenRouter for normal answers, automatic and explicit long-term Memory Review, and rolling
Conversation Summary generation.

The normal answer call returns the user-facing answer and the hidden `MemoryAction` in one structured
response. There is no keyword parser, command list, or separate intent-only LLM call. Generated
`UPDATE`, `FORGET`, and confirmed `DELETE_ALL` actions flow into the Orchestrator and Memory pipeline
already merged at `pia-harness/main@c4005ce`.

Plan author and initial implementation Agent: Codex. Independent plan and implementation reviewer:
Claude. The owner may change either role; this branch remains the plan-to-implementation branch.

## Current state

- Session, Turn persistence, rolling-summary Compaction, bounded long-term Memory, prompt assembly,
  interruption-aware orchestration, and generated `MemoryAction` routing are implemented.
- `PromptContextAssembler` needs a callable that counts the complete rendered request.
- `ConversationOrchestrator` needs an async `generate_answer` callable.
- `AutomaticMemoryReviewer` needs one synchronous `review` callable shared by automatic and explicit
  paths.
- `TokenCompactor` needs one synchronous `summarize` callable.
- Tests currently supply fakes for all four seams; no real model request exists in `pia-harness`.
- `/opt/pia/app/openrouter.py` is the older application-specific plain-text client. This feature does
  not import or duplicate application configuration from `pia`; the later integration replaces or
  adapts it using this library component.

## Ownership boundary

`pia-harness` owns:

- OpenRouter request construction and safe HTTP behavior;
- trusted/untrusted prompt rendering;
- strict structured-output schemas and local response validation;
- mapping responses into `GeneratedAnswer`, `MemoryReviewOutput`, `SummaryOutput`, and `ContextUsage`;
- conservative pre-request token estimation over the actual rendered payload;
- client lifecycle and mocked tests.

`pia` later owns:

- reading the API key from AWS Secrets Manager;
- choosing the deployed exact model ID, context limit, timeout, and user-facing failure notice;
- constructing the Harness store, assembler, reviewer, compactor, adapter, and orchestrator;
- Telegram identity/delivery, DynamoDB table/IAM, deployment, and live E2E validation.

No secret, AWS resource, Telegram behavior, or running Bot changes in this feature.

## Minimal public API

Add one provider module and one primary adapter:

    OpenRouterModelAdapter(
        api_key,
        model_id,
        token_budget,
        timeout_seconds,
        sync_client=None,
        async_client=None,
    )

It exposes the existing callable shapes directly:

    count_input_tokens(parts) -> int
    async generate_answer(context) -> GeneratedAnswer
    review_memory(request) -> MemoryReviewOutput
    summarize(request) -> SummaryOutput

Use one configured `model_id` and one immutable `ModelTokenBudget` for every operation. Do not introduce
separate answer, Memory, Summary, context-limit, or output-reserve settings in this version. Summary
still honors `SummaryRequest.max_output_tokens`; answers and Memory Review use the shared response
reserve.

Add a safe `OpenRouterModelError` containing only a stable event, optional HTTP status, and optional
exception type. It must never retain or chain a request, headers, API key, prompt, response body, or
user content.

The adapter owns default sync and async HTTP clients when callers do not inject them. Provide explicit
cleanup for owned clients. Injected clients remain caller-owned. Claude should check that the smallest
sync/async lifecycle is clear, since answer generation is async while Memory Review and Summary are
currently synchronous and run in the Orchestrator's durable executor path.

## OpenRouter request policy

- Endpoint: `POST /api/v1/chat/completions` through a default base URL of
  `https://openrouter.ai/api/v1`.
- Authentication: injected Bearer API key. Reject an empty key at construction and never expose it.
- Non-streaming requests only in this first adapter.
- Use `max_completion_tokens`, not the legacy `max_tokens` field.
- Use `response_format.type = json_schema`, `strict = true`, and `additionalProperties = false` for
  every operation.
- Set `provider.require_parameters = true` so OpenRouter routes only to endpoints that support the
  requested structured output.
- Do not enable response healing, plugins, tools, web search, fallback models, automatic retries, or
  provider-specific routing preferences.
- Missing endpoint support, timeout, transport failure, non-2xx status, refusal, empty content,
  malformed JSON, schema mismatch, and invalid usage fail through the safe adapter error.

OpenRouter currently documents structured output only for compatible model endpoints and recommends
`require_parameters=true`. Strict mode still requires local validation because endpoint enforcement can
vary. OpenRouter response usage includes native `prompt_tokens`, `completion_tokens`, and
`total_tokens`; no separate usage lookup is needed.

Primary references:

- https://openrouter.ai/docs/guides/features/structured-outputs
- https://openrouter.ai/docs/guides/routing/provider-selection
- https://openrouter.ai/docs/cookbook/administration/usage-accounting
- https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request

The adapter does not call the Models API at startup. Model capability changes are handled by the
request's required-parameter routing and safe failure. The caller supplies the model's approved context
limit through `ModelTokenBudget`, avoiding an extra network dependency and a second source of runtime
configuration.

## Normal answer and Memory action

Render `AssembledPromptContext.parts` in order:

- `SYSTEM` is the sole trusted system message.
- Append one fixed trusted adapter instruction describing the structured answer contract and explicit
  Memory-action rules.
- `MEMORY` and `SUMMARY` become clearly labelled user-context data, never system instructions.
- `USER_TURN`, `ASSISTANT_TURN`, and `CURRENT_USER` keep their conversation roles.
- Preserve content exactly inside the labels; do not interpret, sanitize, or silently truncate it.

The strict answer schema is:

    {
      "answer": string,
      "memory_action": "NONE" | "UPDATE" | "FORGET" | "DELETE_ALL",
      "delete_all_confirmed": boolean
    }

Action instruction:

- `UPDATE` only when the current user explicitly asks PIA to retain or durably change user context,
  including natural durable preferences such as “앞으로 답변은 핵심 위주로 해줘”.
- `FORGET` only for an explicit targeted request to stop retaining particular user context.
- `DELETE_ALL` for a request concerning the complete Memory document. The first request sets
  `delete_all_confirmed=false` and asks naturally for confirmation. Only an affirmative response to the
  immediately preceding complete-deletion question sets it true.
- `NONE` for normal conversation, Memory-description questions, automatically learnable statements,
  and ambiguous language. The answer can clarify naturally.
- Never emit a Memory action merely because words such as “기억”, “잊어”, or “앞으로” appear. Decide
  from meaning, not keywords.
- The answer must not claim that an update, forget, or deletion already persisted. The Orchestrator
  appends the confirmed change or required failure notice after the durable operation.

Parse the result into `GeneratedAnswer`. Use the response's non-empty model identifier consistently for
both `GeneratedAnswer.model_id` and `ContextUsage.model_id`. When valid usage is present, set
`ContextUsage.total_tokens` and use it as the generated answer's total estimate. If usage is absent,
keep `usage=None` and conservatively estimate the rendered request plus returned content.

## Shared Memory Review call

`review_memory` is the one callable injected into `AutomaticMemoryReviewer`, so automatic revisit,
pre-Compaction/pre-reset, explicit `UPDATE`, and explicit `FORGET` all use the same adapter path.

Render `MemoryReviewRequest.instruction` as the trusted system instruction. Render current Memory,
prior completed Turns, optional current input, character bound, and `allow_clear` as labelled data in a
user message. Do not promote any stored or conversational text into the system role.

The strict schema maps directly to the existing domain type:

    {
      "action": "UNCHANGED" | "REPLACE" | "CLEAR",
      "memory_text": string | null,
      "change_summary": array[string]
    }

Schema bounds mirror the domain contract where supported: Memory text at most 4,000 characters,
change summary at most three items and 200 characters each. The existing
`AutomaticMemoryReviewer._apply` remains authoritative for semantic validation, CLEAR permission, CAS,
and storage. The adapter parses; it does not create a second validation or persistence pipeline.

No second Memory Review is added for explicit requests. `review_explicit_input` already sends prior
unreviewed Turns and the accepted current input together to this same callable.

## Rolling Summary call

Render `SummaryRequest.instruction` as the trusted system message. Render the previous Summary and
covered Turns as labelled untrusted data in chronological order.

Use a strict one-field schema:

    { "summary": string }

Return `SummaryOutput` with the response model ID and `usage.completion_tokens` when present. Otherwise
use the existing conservative estimator for `token_count`. `TokenCompactor` remains authoritative for
non-empty output, size reduction, output reserve, summary CAS, and raw-Turn deletion.

## Token counting

OpenRouter reports native usage only after a completion. Pre-request assembly therefore uses the
existing conservative UTF-8-byte estimator over a deterministic compact serialization of the exact
rendered answer messages plus its fixed structured-output schema and provider framing fields that affect
model input.

The same renderer must be used by `count_input_tokens` and `generate_answer`; tests compare their exact
payloads so the counter cannot estimate one representation while the request sends another. Provider
`total_tokens` remains preferred after a successful answer and feeds the existing Compaction decision.

Do not add a tokenizer package, remote token-count request, or model-name heuristic in this version.

## HTTP and concurrency behavior

- `generate_answer` uses the async client so task cancellation reaches the in-flight HTTP request. The
  Orchestrator's generation ID remains the correctness boundary if cancellation is ignored.
- `review_memory` and `summarize` use the sync client because existing Memory and Compaction callables
  execute after commit ownership in worker threads and are deliberately non-interruptible.
- One adapter instance may serve different users concurrently. Do not keep request-specific mutable
  state on the adapter.
- Configure one bounded timeout. Do not retry automatically; the existing Orchestrator failure policy
  decides whether the answer, Memory, or Compaction can continue.

## Validation and safe errors

Validate at construction:

- non-empty API key and exact model ID;
- `ModelTokenBudget` instance;
- positive finite timeout;
- injected client types or required callable behavior.

Validate every response before constructing domain types:

- one usable choice and non-empty message content;
- content is a JSON object with exactly the schema fields;
- enum and boolean types are exact, not truthy coercions;
- strings and arrays satisfy schema/domain bounds;
- model ID is non-empty;
- usage is absent or contains non-negative integer counts with booleans rejected and internally
  consistent totals.

Do not include response bodies in exceptions. Tests use a synthetic key and assert it, prompts, and
headers never appear in `str(error)`, `repr(error)`, or chained causes.

## Dependency and package changes

- Add a bounded `httpx` runtime dependency to `pyproject.toml`.
- Add the adapter module and export only the intended public adapter, error, and any small immutable
  configuration type that earns its use.
- Update `README.md` with construction, callable wiring, cleanup, and explicit note that secrets and AWS
  integration belong to the consuming application.
- Do not copy `/opt/pia/app/openrouter.py`; implement the Harness contract against current domain types.

## Tests

Use `httpx.MockTransport` or equivalent injected clients. No live OpenRouter request or credential is
used.

Required focused coverage:

1. exact answer message roles, labels, order, schema, requested model, output limit, and
   `require_parameters`;
2. normal Korean answer parsing for every `MemoryAction` and confirmation combination;
3. ambiguous or ordinary text maps only from the returned `NONE`; no keyword parser exists;
4. answer usage maps consistently into `GeneratedAnswer` and `ContextUsage`;
5. missing usage uses the conservative estimate without inventing provider usage;
6. `count_input_tokens` uses the same answer renderer and includes the structured schema overhead;
7. Memory Review renders current Memory, prior Turns, and optional current input as untrusted data and
   maps all three review actions;
8. Memory Review uses the same callable for automatic and explicit caller tests without branching by
   trigger source;
9. Summary renders previous Summary and Turns and maps completion token count;
10. empty, malformed, extra-field, wrong-type, refusal, invalid enum, and invalid usage responses fail
    closed;
11. transport, timeout, HTTP status, and cancellation behavior;
12. synthetic API key, prompts, headers, and response bodies never appear in safe errors;
13. injected clients are not closed by the adapter; owned sync and async clients are closed once;
14. the existing Orchestrator, Memory, Compaction, Context, and DynamoDB suites remain green.

Run focused adapter tests first, then the complete Python suite against DynamoDB Local once. Run the
existing Ruff checks. Do not make a live model call as part of automated verification.

## Out of scope

- string/regex intent parsing or an intent-only model call;
- OpenRouter Models API discovery or dynamic context-limit updates;
- multiple model IDs, provider fallbacks, retries, response healing, streaming, tools, web search, or
  Agent SDK integration;
- prompt caching, generation-history lookup, price accounting, quota, or billing policy;
- embeddings, vector Memory, Memory history, confidence scoring, or Memory administration UI;
- changes to Memory storage, 4,000-character policy, automatic Review schedule, CAS, Compaction policy,
  interruption behavior, or delete-confirmation state;
- `pia-agent`, Telegram, AWS, Secrets Manager, DynamoDB infrastructure, deployment, or live E2E work.

## Implementation order after review

1. Add the bounded HTTP dependency, safe error, client construction, and lifecycle.
2. Implement shared rendering and conservative request counting.
3. Implement structured normal answer generation with `MemoryAction`.
4. Implement the shared Memory Review and rolling Summary callables.
5. Add mocked focused tests and update exports and `README.md`.
6. Run focused tests, Ruff, and the full DynamoDB Local suite once.
7. Commit and push implementation on this same branch for Claude's final review.

## Claude review questions

1. Is a provider adapter inside the reusable Harness the correct boundary while secrets, AWS wiring,
   Telegram, and deployment remain in `pia`?
2. Are one model ID and one `ModelTokenBudget` sufficient for answer, Memory, and Summary without
   duplicated configuration?
3. Is one adapter with async answer and synchronous Review/Summary the smallest fit for the current
   callable contracts, or is its lifecycle unnecessarily awkward?
4. Does the message rendering preserve the Assembler trust boundary and conversation ordering?
5. Is strict JSON Schema plus `require_parameters=true` and local validation sufficient without a
   Models API preflight, response healing, or fallback parser?
6. Does the answer action instruction preserve the agreed explicit-only behavior while leaving automatic
   learning to the existing reviewer schedule?
7. Is conservative counting over the exact rendered request the correct preflight fallback while
   provider usage remains authoritative after completion?
8. Are any fields, checks, lifecycle methods, or tests unnecessary for this first adapter, or is a
   critical contract missing?

If a blocker exists, propose the smallest correction. Do not add an intent-only LLM call, keyword
parser, extra model configuration, tokenizer dependency, provider hierarchy, retry framework, queue,
worker, outbox, vector database, `pia-agent` change, AWS work, deployment, or live credential use.

## Review record: 2026-09-10, Claude, plan commits d7f7c05 and 98a957f

Plan-only review against merged `main` at `c4005ce`. Nothing was implemented and nothing was merged.
Two blockers, both where this plan meets an existing contract rather than in its own design. The
boundary, the shared configuration, the trust rendering and the structured-output policy are all right
and need no change.

What holds. Putting the adapter in the Harness is the correct split: the Harness owns the four callable
shapes and the rendering that makes them true, while secrets, the deployed model choice, the table and
delivery stay in `pia`, which is the same line every earlier feature drew. Nothing binds to a model
name: the ID, the context limit and the timeout all arrive from the caller, there is no tokenizer
package, no per-model branch, and no Models API call at startup. One model ID and one
`ModelTokenBudget` are enough for all three operations, and requiring the same renderer for
`count_input_tokens` and `generate_answer` — with a test comparing the exact payloads — is what makes
the assembler's budget mean anything, since that budget stopped reserving separate input space on the
condition that the counter measures the whole real request. Async answer with synchronous Review and
Summary matches the existing seams exactly: the answer must be cancellable for interruption, while
Review and Summary already run in the durable executor path after commit ownership, where cancellation
is deliberately not wanted. The trust rendering keeps `SYSTEM` plus a fixed adapter instruction as the
only trusted content and puts Memory, Summary and conversation in labelled user data, which is the rule
the Assembler review asked the adapter to honor.

## Blocker 1: the Memory Review output cap contradicts the Memory bound

The plan caps answers and Memory Review at the shared response reserve, currently 4,096 tokens. Memory's
authoritative bound is 4,000 *characters*, and those two units do not convert in the safe direction for
this product's language. A 4,000-character Korean document needs at least that many tokens in any
common tokenizer, plus the JSON envelope, escaping and up to three 200-character change-summary items.
So a Memory document near its allowed size cannot fit in the allowed completion.

The failure is not a truncated document; it is a truncated JSON body, which fails schema validation and
fails closed. That is the correct behavior for one bad response but the wrong outcome here, because it
is deterministic: once a user's Memory approaches the bound, every automatic revisit, every
pre-Compaction flush and every explicit remember or forget fails, Memory freezes at whatever size it
reached, and each explicit request returns the required failure notice. The user's Memory silently stops
working the more the product has learned about them.

Smallest correction, and it removes a setting rather than adding one:

    Memory Review does not send a completion cap. Its output is bounded by the strict schema and by the
    existing local 4,000-character validation in `AutomaticMemoryReviewer._apply`, which is already
    authoritative and rejects an oversized document without storing it.

The shared reserve still governs the answer, and Summary keeps honoring `SummaryRequest.max_output_tokens`,
whose bound and validation are both already in tokens and therefore consistent. Do not solve this by
adding a second output-reserve setting; that is the duplicated configuration the previous refactor
deliberately removed.

## Blocker 2: estimating `SummaryOutput.token_count` re-opens a fixed defect

The plan says to fill `token_count` from `usage.completion_tokens` when present and otherwise from the
conservative estimator. That second half reintroduces a bug already fixed on `main`.

`TokenCompactor` treats a present `token_count` as a provider-reported token count and compares it with
the response reserve. The conservative estimator counts UTF-8 bytes, and Korean is three bytes per
character, so a normal 1,500-character Korean summary estimates about 4,500 against a 4,096 limit and is
rejected. That is exactly the failure the compaction review found and closed by keying the check on
`output.token_count is not None`, so that an estimated summary skips a check it cannot satisfy. Filling
the field with an estimate defeats that fix and returns compaction to failing on every attempt for
Korean conversations until a provider reports usage.

Smallest correction:

    Set `SummaryOutput.token_count` only from provider usage. Leave it `None` when usage is absent;
    `TokenCompactor` then computes its own estimate for the size-reduction check and correctly skips the
    token-based output check.

The answer path is different and the plan is right there: `GeneratedAnswer.estimated_total_tokens` is
required, a conservative byte estimate only makes Compaction trigger early, and early is the safe
direction.

## Smaller notes, no plan change required

`MemoryReviewOutput.change_summary` must be a tuple; `_validated_output` rejects a list outright, and a
JSON array parses to a list, so the mapping has to convert. The shared `ModelTokenBudget` is only true
for one model, so the caller must configure an exact model ID rather than a routing alias whose
effective model, and therefore context window, can change between requests; that belongs in the README
note the plan already promises. And the "small immutable configuration type that earns its use" should
stay unwritten unless something needs it, since the model ID, budget and timeout are already
constructor arguments.

## Answers to the review questions

1. Correct boundary, and consistent with where every earlier feature drew it.
2. Yes, and sharing them is what keeps the assembler budget and the compaction trigger derived from one
   number.
3. Yes. Async answer and synchronous Review and Summary match the existing seams; the only lifecycle
   cost is two owned clients, which explicit cleanup covers.
4. Yes, with the trust rule stated the way the Assembler review asked.
5. Yes. Strict schema, required parameters and local validation are enough precisely because local
   validation is not skipped when the endpoint claims strictness.
6. Yes. The instruction is explicit-only, tells the model to decide from meaning rather than keywords,
   and forbids claiming a persisted change, which is what leaves automatic learning to the reviewer
   schedule and the change notice to the Orchestrator.
7. Yes for the answer, once blocker 2 keeps the same estimator out of the Summary token field.
8. Nothing is excessive; the validation list is all fail-closed checks on untrusted provider output. The
   two missing contracts are the blockers above; add one test each: a Memory Review whose document sits
   near the character bound completes rather than truncating, and a Summary without provider usage
   leaves `token_count` unset.

## Instructions for Codex

Apply the two corrections to the plan text before implementing, and add the two verification cases
named above. Nothing else needs to change: no intent-only call, keyword parser, extra model
configuration, tokenizer dependency, provider hierarchy, retry framework, or live credential belongs in
this feature.
