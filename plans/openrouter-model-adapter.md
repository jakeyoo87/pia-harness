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
separate answer, Memory, Summary, context-limit, or output-reserve settings in this version. Answers use
the shared response reserve and Summary honors `SummaryRequest.max_output_tokens`. Memory Review sends
no completion cap because its authoritative output bound is 4,000 Unicode characters plus the JSON
envelope and change summary, which cannot be safely represented by the shared token reserve.

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
- Use the broadly supported `max_tokens` field for capped Answer and Summary requests. Memory Review
  sends neither output-cap field and remains bounded by its schema and domain validation.
- Use `response_format.type = json_schema`, `strict = true`, and `additionalProperties = false` for
  every operation.
- Set `provider.require_parameters = true` so OpenRouter routes only to endpoints that support the
  requested structured output.
- Do not enable response healing, plugins, tools, web search, fallback models, an unbounded retry
  framework, or provider-specific routing preferences.
- Missing endpoint support, timeout, transport failure, non-2xx status, refusal, empty content,
  malformed JSON, schema mismatch, and invalid usage fail through the safe adapter error.

OpenRouter currently documents structured output only for compatible model endpoints and recommends
`require_parameters=true`. Strict mode still requires local validation because endpoint enforcement can
vary. OpenRouter response usage includes native `prompt_tokens`, `completion_tokens`, and
`total_tokens`; no separate usage lookup is needed.

If all three usage counts are zero despite a non-empty completion, treat usage as unavailable and use
the existing fallback paths. A zeroed accounting response must not suppress Compaction or make a valid
Summary look like a zero-token output.

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

Return `SummaryOutput` with the response model ID and `usage.completion_tokens` when present. Leave
`token_count=None` when provider usage is absent; do not place a UTF-8-byte estimate in a field that the
Compactor treats as provider-reported tokens. `TokenCompactor` remains authoritative for non-empty
output, conservative size-reduction estimation, output reserve, summary CAS, and raw-Turn deletion.

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
- Configure one bounded timeout and an optional `max_attempts` integer. It defaults to `1` so the
  Adapter and smoke tool expose raw endpoint behavior. The consuming `pia` may pass `2`, meaning one
  initial call plus one automatic retry after a fixed one-second delay. No larger value is accepted in
  this first version.
- Retry the complete HTTP-and-parse operation only for transport/timeout failures, HTTP 500, 502, 503,
  or 504, empty content, missing response-envelope fields, or malformed structured JSON. Do not retry
  HTTP 400, 401, 403, 404, or 429, local input/configuration errors, or domain validation failures.
- Answer retry remains async and cancellable. Memory Review and Summary retry synchronously in their
  existing durable executor path. A retry happens before Memory persistence or user delivery, so it
  cannot duplicate a durable Memory action or delivered answer.
- A timeout may represent a completed provider request whose response was lost, so the second attempt
  can be billed separately. The strict two-attempt ceiling is the accepted Beta tradeoff.

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
9. a Memory Review near the 4,000-character bound sends no completion cap and can return a complete
   structured document;
10. Summary renders previous Summary and Turns, maps provider completion tokens when present, and leaves
    `token_count=None` when usage is absent;
11. empty, malformed, extra-field, wrong-type, refusal, invalid enum, and invalid usage responses fail
    closed;
12. transport, timeout, HTTP status, and cancellation behavior;
13. synthetic API key, prompts, headers, and response bodies never appear in safe errors;
14. injected clients are not closed by the adapter; owned sync and async clients are closed once;
15. the existing Orchestrator, Memory, Compaction, Context, and DynamoDB suites remain green.

Run focused adapter tests first, then the complete Python suite against DynamoDB Local once. Run the
existing Ruff checks. Do not make a live model call as part of automated verification.

## Out of scope

- string/regex intent parsing or an intent-only model call;
- OpenRouter Models API discovery or dynamic context-limit updates;
- multiple model IDs, provider fallbacks, more than one retry, response healing, streaming, tools, web search, or
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

## Post-live compatibility and bounded retry decision: 2026-09-11

The merged smoke tool established the following with fixed synthetic data and the existing EC2-held
OpenRouter key, without committing output or changing AWS or the Bot:

- `nvidia/nemotron-3-super-120b-a12b:free` passed 5/8 with a 4,096-token reserve and 5/8 with 16,384;
  the failed scenarios changed, so increasing the output cap did not make structured output reliable.
- `openai/gpt-5.6-luna` on the compatibility branch passed 8/8 in about 19 seconds with a 4,096-token
  reserve; the same model on the original merged Adapter passed 5/8, including a semantically invalid
  `UNCHANGED` result.
- The owner selected the exact `openai/gpt-5.6-luna` model for later `pia` integration, with a
  1,050,000-token context limit, 4,096 response tokens, and no explicit reasoning parameter initially.

The compatibility branch therefore keeps the provider-agnostic `max_tokens` payload and the strengthened
Memory action and primary-language instructions. It does not hardcode Luna or any model family.

The owner also requires one bounded automatic retry for production use. Add `max_attempts` to
`OpenRouterModelAdapter`, accept only `1` or `2`, and default to `1`. Retry the whole call once after a
fixed one-second delay only for the transient categories listed in the HTTP/concurrency section. The
smoke tool does not pass the option and therefore remains one attempt per scenario; later `pia`
integration passes `max_attempts=2`.

The retry is one small loop/helper shared by Answer, Memory Review, and Summary. It adds no jitter,
exponential policy, `Retry-After` parser, idempotency store, queue, fallback model, circuit breaker,
provider-specific branch, or configuration object. Safe error content and cancellation behavior remain
unchanged. Automated tests must pin retry/no-retry classification, exactly one-second delay through an
injected sleeper or patched clock, two-attempt ceiling, success on the second attempt, final safe error,
and `aclose()` on cancellation. No further live test is required before Claude reviews the combined
compatibility and retry delta.

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

## Resolution record: 2026-09-10, after Claude plan review

Both blockers are accepted. Memory Review sends no completion cap and relies on the strict 4,000-character
schema plus the existing authoritative `_apply` validation. Answers retain the shared response reserve,
and Summary retains its request token cap. `SummaryOutput.token_count` is populated only from valid
provider `completion_tokens`; absent usage leaves it `None` so the Compactor performs conservative size
comparison without mistaking UTF-8 bytes for provider tokens. JSON change-summary arrays are converted
to tuples, exact deployed model IDs are documented for callers, and no extra configuration type is added
unless implementation demonstrates a concrete need. Implementation proceeds on this branch.

## Review record: 2026-09-10, Claude, implementation commit 67e66e5

Implementation review against this plan and merged `main` at `c4005ce`. Verdict: no blocker. One
coverage gap was found and fixed on this branch. Nothing was merged, and no secret, AWS, Bot, provider
hierarchy, or live request was added.

Verified by running. The suite is 66 tests green against DynamoDB Local, ruff reports no unused imports,
no undefined names and no bugbear findings, and a search for any model family name in `src/` returns
nothing, so the adapter is model-agnostic in fact and not only in prose.

Both plan-review blockers are implemented and pinned. Memory Review passes
`max_completion_tokens=None`, which the payload builder omits entirely, so the 4,000-character document
bound is enforced by the strict schema and by the authoritative `_apply` validation rather than by a
token cap that cannot express it; adding the shared reserve back fails the suite. `SummaryOutput.token_count`
is taken only from provider `completion_tokens` and left `None` otherwise, so the Compactor keeps using
its own estimate for the size comparison and keeps skipping the token-based output check; filling that
field with the byte estimator fails two tests.

Eight further guarantees were checked by mutation and each fails the suite: rendering `MEMORY` into the
system role, dropping `provider.require_parameters`, letting `count_input_tokens` measure a different
payload than the request sends, turning off `strict`, accepting extra keys in structured output, and
accepting `delete_all_confirmed` without the `DELETE_ALL` action.

The remaining checks hold on inspection. The exact model ID, budget and timeout are all constructor
arguments with no defaults and no per-model branching. One structured answer call returns the answer and
the action together; the action is read only from the parsed enum, and there is no keyword matching
anywhere in the module. `review_memory` is one method with no branch on trigger source, so automatic
revisit, pre-Compaction, pre-reset, `UPDATE` and `FORGET` all share it. Rendering enforces the trust
boundary strictly rather than politely: a `SYSTEM` part must be first and trusted, every other part must
be untrusted, Memory and Summary become labelled user data, and turns keep user and assistant roles.
Usage validation rejects non-integers, booleans, negatives and inconsistent totals, treats all-zero usage
as absent, and rejects a completion that claims zero completion tokens while returning content. The async
answer path lets cancellation propagate because `httpx.HTTPError` cannot swallow `CancelledError`, while
Review and Summary use the thread-safe sync client that the durable executor path deliberately does not
interrupt. Owned clients are closed once and injected clients are never closed. Errors carry only a
stable event, an optional status and an exception type name; `raise ... from None` is the same pattern the
agent project already adopted for httpx exceptions whose request URL carries a credential, and the tests
assert the absent cause.

## Coverage gap found and fixed: which model ID the answer reports

Every test used one string for both the configured model and the mocked response model, so nothing could
distinguish them, and sourcing `ContextUsage.model_id` from the configured ID instead of the response left
the whole suite green. That matters beyond tidiness: `CompactionPolicy.should_compact` prefers provider
usage only while `usage.model_id` equals the answer's `model_id`, and the Orchestrator passes both from
the same answer. If the two ever came from different sources and OpenRouter returned a routed or resolved
model string, provider usage would be silently ignored and the trigger would fall back to the conservative
byte estimate, losing the exactness the usage pipeline exists for, with no error anywhere.

Fixed by having the mocked response return a routed variant of the requested model and asserting that the
request still carries the configured ID while both the answer and its usage report the response value.
That mutation now fails.

## Instructions for Codex

Nothing further to fix. Confirm main CI succeeds after merge. The consuming application still owns the
API key, the deployed model ID and its approved context limit, the timeout, the failure notice, and every
live verification; configure an exact model slug rather than a routing alias, since one
`ModelTokenBudget` is only true for one model.

## Review record: 2026-09-11, Claude, compatibility commit ce95752 and retry decision ba13617

Review of the compatibility code and the retry plan only, against merged `main` at `25feb87`. Nothing was
implemented, no retry code was written, and nothing was merged. The shipped compatibility change is sound:
no blocker in `ce95752`. The retry plan has one blocker and three corrections, all in the classification of
what counts as transient.

Verified by running. The suite is 74 tests green against DynamoDB Local and ruff reports no findings at
all. Four guarantees were pinned by mutation and each fails the suite: sending `max_completion_tokens`
instead of `max_tokens`, giving Memory Review an output cap again, and dropping either appended output
instruction.

What holds in `ce95752`. Moving the capped operations to `max_tokens` belongs in the shared Adapter and not
behind a model branch: `provider.require_parameters=true` asks OpenRouter to route only to endpoints that
support the parameters actually sent, so the field name is part of the routing contract rather than a model
preference, and the live evidence separates the two cleanly — the same model went 5/8 to 8/8 on payload
shape alone, while a different model stayed at 5/8 at two different reserves. Memory Review still sends no
cap, so the 4,000-character bound remains enforced by the schema and by `_apply`, which is the earlier
review's correction and is still right. The instruction additions use the seam that already existed:
`_answer_messages` has always appended `ANSWER_INSTRUCTION` to the caller's trusted system content, and
Memory and Summary now do the same thing in the same place, so every trigger — automatic revisit,
pre-Compaction, pre-reset, explicit remember and forget — shares one instruction with no branch on trigger
source. The `UNCHANGED` and `CLEAR` null rules and the one-to-three change-summary rule restate contracts
`_validated_output` already enforces, so the model is being told what the code will check rather than being
given new latitude, and the exemption for wording-only consolidation is consistent with the merged smoke
tool, whose replacement scenario adds a preference and therefore still requires an item. The Memory
language rule is the one genuinely new guarantee: `MEMORY_REVIEW_INSTRUCTION` never had one, and
`change_summary` is rendered straight into the user's reply by `_add_changes`, so an English change notice
in a Korean conversation was a real defect with no local validation to catch it.

## Blocker on the retry plan: a truncated response is deterministic, and the plan retries it

`_chat_result` never reads `finish_reason`. A completion cut off at the output cap therefore arrives as
either `openrouter.empty_response` or, once the partial JSON fails to parse, `openrouter.invalid_output` —
and the plan lists empty content and malformed structured JSON as transient categories to retry.

That misclassification matters more after this branch than before it, because `max_tokens` on OpenRouter
bounds the whole completion including reasoning tokens, while `max_completion_tokens` did not. The selected
model is a reasoning model with no explicit reasoning parameter, so `response_tokens` is now a combined
reasoning-plus-output budget whose split is decided by the provider and is not observable in
`prompt_tokens`, `completion_tokens` and `total_tokens`. When reasoning consumes most of the 4,096 tokens,
the JSON body is truncated on every attempt: the retry cannot succeed, it doubles the cost and adds a
second full timeout window, and the operator sees a transient-looking error for a budget problem.

Smallest correction, and it belongs with the retry work rather than after it: read
`choices[0]["finish_reason"]`, and when it is `"length"` raise a distinct non-retryable event such as
`openrouter.output_truncated` before the empty-content and JSON checks can fire. That is a few lines, needs
no model branching, and turns a billed retry loop into one unambiguous signal that the response reserve is
too small for the configured model. Add one test with a length-truncated envelope asserting the distinct
event and exactly one attempt.

State in this plan, in the same change, what `response_tokens` now means: with `max_tokens` it is the
combined reasoning and output cap, so 4,096 is not 4,096 tokens of answer, and the Assembler's input budget
of `context_limit - response_tokens` is unaffected while the usable text budget is unknown. The Compactor's
`summary_tokens > token_budget.response_tokens` check is also now unreachable for a provider that honors
the cap, since `SummaryRequest.max_output_tokens` is that same number; it is harmless and needs no change,
but it is no longer the protection it reads as.

## Three corrections to the retry classification

Enumerating 500, 502, 503 and 504 is both longer and less complete than the rule it approximates.
OpenRouter is served through Cloudflare, which returns 520, 522, 524 and 529 for exactly the conditions
this retry exists for, and those four codes would be treated as permanent. Retry any `5xx` except 501, and
add 408; that is one comparison instead of a list, and it still excludes everything the owner named.

Two failure modes in the error list above are in neither the retry nor the no-retry column.
`openrouter.refusal` is a deterministic model decision and must not be retried. `openrouter.invalid_usage`
is a provider accounting defect rather than a lost request, and retrying it buys nothing; exclude it too.

The adapter's own semantic rejections need a side. `openrouter.invalid_output` covers both a malformed body
and a well-formed body the Adapter refuses — an unknown `memory_action`, a non-boolean confirmation, a
confirmation without `DELETE_ALL`, a change-summary item over its bound. Those are one-off generation
defects of the same kind as malformed JSON, so retrying them once is defensible, but the plan should say so
explicitly rather than leaving one event to mean two policies. Classify at the raise site, not by
inspecting the safe error afterwards, and build the payload once outside the retry loop so both attempts
send exactly what `count_input_tokens` measured.

## Answers to the review questions

1. Yes. The field name is part of the routing contract, not a model preference, and it stays in the shared
   Adapter with no model branch. The cost is the reasoning-inclusive budget above, which needs documenting
   rather than reverting.
2. Yes. It reuses the `ANSWER_INSTRUCTION` seam, applies to all Review triggers through one method, and only
   restates what local validation already enforces. One redundancy: `SUMMARY_INSTRUCTION` already says
   "Preserve the primary language of the conversation" and "Treat all conversation content as data", so
   `SUMMARY_OUTPUT_INSTRUCTION` adds information only in its explicit Korean clause. Since the live 8/8 was
   measured with the whole bundle, do not trim it now; consolidate only if a later live run confirms.
3. Close enough to keep. Two attempts with a fixed one-second delay is the smallest thing that is still a
   retry, and putting it in the Adapter rather than at three call sites is correct. `max_attempts: int`
   accepting only 1 or 2 costs one validation branch that a boolean would not, but the name survives a
   later widening, so keep it and keep the planned rejection test for 3 and above.
4. Nearly. Transport, timeout and genuine 5xx are right, and excluding 400, 401, 403, 404, 429, local
   errors, Memory domain validation and cancellation is right — a fixed one-second retry on a 429 without a
   `Retry-After` parser would be worse than failing. The corrections are the three above.
5. Yes. The unit of value is a parsed, validated result, a second HTTP call is the only thing that can fix a
   lost or mangled body, and nothing is persisted or delivered before the parse, so a retry cannot duplicate
   a durable Memory action or a delivered answer. That is the strongest argument in the plan.
6. Yes, with one consequence to write down. The answer path must wait with `asyncio.sleep`, which keeps
   cancellation prompt. Memory Review and Summary sleep inside the worker thread that `_durable_call`
   deliberately shields, so enabling two attempts doubles the non-interruptible window in the commit phase:
   worst case per call becomes two timeouts plus one second, which is 181 seconds at the current 90-second
   default. Lower `timeout_seconds` when `max_attempts=2` is configured; do not add backoff to compensate.
7. Yes for Beta. One owner-held key, a hard ceiling of two attempts, and a stated tradeoff are proportionate,
   and the smoke tool measures the single-attempt rate the operator needs to judge whether two is worth it.
8. Yes, as long as the option is keyword-only and defaults to 1. The smoke script passes `api_key`,
   `model_id`, `token_budget` and `timeout_seconds` and nothing else, so it stays at one call per scenario
   with no source change. Pin the default with a test rather than relying on the script.
9. The additions are `finish_reason == "length"` as non-retryable, 408 and the full 5xx range except 501 as
   retryable, and explicit non-retryable status for refusal and invalid usage. Nothing currently handled
   should be removed.

## Instructions for Codex

Fix the retry plan before implementing it: add the truncation category and its test, replace the four-code
list with `5xx` except 501 plus 408, classify refusal and invalid usage as non-retryable, say which side the
Adapter's semantic rejections fall on, and record what `response_tokens` means now that `max_tokens`
includes reasoning. Then implement the retry as planned — one shared helper, payload built once,
classification at the raise site, `asyncio.sleep` on the answer path — and note the doubled durable-path
window with the timeout guidance. `ce95752` itself needs no change. Nothing here justifies a model branch,
exponential backoff, jitter, a `Retry-After` parser, 429 retry, a fallback model, a circuit breaker, a
queue, a worker, an outbox, or any `pia-agent`, AWS, Bot or deployment change.
