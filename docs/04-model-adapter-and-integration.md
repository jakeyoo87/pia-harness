# Model Adapter and integration

## Adapter responsibility

`OpenRouterModelAdapter` is model-agnostic. It receives an exact OpenRouter model ID and one
`ModelTokenBudget`; it contains no model family name, Models API discovery, pricing policy, fallback
model, or reasoning choice.

It implements the four callables consumed by the Harness:

```text
count_input_tokens(parts)              local conservative preflight
await generate_answer(context)         provider Answer + MemoryAction
review_memory(request)                 provider Memory Review
summarize(request)                     provider rolling Summary
```

Only the final three call OpenRouter.

## Request and output contracts

Every provider call uses OpenRouter Chat Completions, non-streaming mode, strict JSON Schema,
`additionalProperties=false`, and:

```json
{
  "provider": {"require_parameters": true}
}
```

Answer and Summary use `max_tokens`. Memory Review sends no token cap because a complete 4,000-character
Memory plus JSON and change notices cannot be safely bounded by the default 4,096-token answer reserve.
Its schema and domain validation enforce the character limit.

Answer output:

```json
{
  "answer": "user-facing text",
  "memory_action": "NONE | UPDATE | FORGET | DELETE_ALL",
  "delete_all_confirmed": false
}
```

Memory Review output:

```json
{
  "action": "UNCHANGED | REPLACE | CLEAR",
  "memory_text": "complete document or null",
  "change_summary": ["zero to three items"]
}
```

Summary output:

```json
{"summary": "concise rolling summary"}
```

The Adapter validates envelope, JSON shape, exact fields, enum and boolean types, string and array bounds,
model ID, and optional usage. Memory and Compaction components perform their authoritative semantic and
persistence validation afterward.

## Rendering and language

The Answer renderer accepts only a first trusted `SYSTEM` part. It appends the fixed Memory-action
instruction to that system content. Memory, Summary, history, and current input remain untrusted; Memory
and Summary are labelled as data in user messages, while historical user and assistant roles are
preserved.

Memory Review and Summary use their request instruction as trusted system content and append fixed output
rules. Source Memory, Turns, previous Summary, and current input are compact labelled JSON inside an
untrusted user message.

Memory documents, Memory change notices, and rolling Summaries are instructed to use the source
conversation's primary language. There is no locale setting, language detector, translation pass, or
second model call.

## Token budget and usage

The caller supplies one exact-model budget:

```python
budget = ModelTokenBudget(
    context_limit=1_050_000,
    response_tokens=4_096,
)
```

`count_input_tokens` conservatively counts UTF-8 bytes over the exact rendered Answer messages and
structured-output schema. This is deliberately an overestimate for many languages and may compact early.

After a successful Answer, OpenRouter native usage is preferred. `ContextUsage` uses the response model
ID, and Compaction consumes it only when it matches `GeneratedAnswer.model_id`. Missing or all-zero usage
falls back to the conservative estimate.

`SummaryOutput.token_count` is populated only from valid provider `completion_tokens`. It remains `None`
when usage is absent; the Compactor must not mistake UTF-8 bytes for provider tokens.

OpenRouter `max_tokens` covers reasoning plus final output together. The Adapter currently sends no
`reasoning` parameter, so the selected endpoint's default applies. `finish_reason="length"` becomes the
non-retryable `openrouter.output_truncated` error; the application decides whether its response reserve is
too small.

## Bounded retry

Constructor option:

```python
max_attempts=1  # allowed: 1 or 2
```

- Default `1` performs one request and is used by the smoke tool.
- `2` performs one initial request and at most one retry after a fixed one-second delay.
- Both attempts use the same already-built payload.
- Async Answer waits with `asyncio.sleep` and remains cancellable.
- Sync Memory Review and Summary wait with `time.sleep` inside the Orchestrator's shielded durable worker.

Retryable:

- transport and timeout errors;
- HTTP 408;
- HTTP 5xx except 501;
- empty content or missing response envelope;
- malformed JSON or an Adapter-rejected structured result.

Non-retryable:

- HTTP 400, 401, 403, 404, 429, and 501;
- provider refusal;
- invalid usage accounting;
- `finish_reason="length"`;
- local input/configuration errors;
- downstream Memory semantic/CAS failure;
- cancellation.

Two attempts can mean two billed requests, and a durable sync call can occupy the commit path for two
timeouts plus one second. Choose the application timeout accordingly; do not add unbounded retry,
exponential backoff, jitter, fallback, `Retry-After` handling, or a circuit breaker without a separate
design.

## Safe errors and lifecycle

`OpenRouterModelError` contains only:

- stable event name;
- optional HTTP status;
- optional exception type name;
- internal retryable boolean.

It does not retain or chain the request, Authorization header, API key, prompt, response body, or user
content.

The Adapter owns its default sync and async httpx clients. `await adapter.aclose()` closes both. Injected
clients remain caller-owned; `close()` closes only an Adapter-owned sync client.

## Complete construction example

```python
import boto3

from pia_harness import (
    AutomaticMemoryReviewer,
    ConversationOrchestrator,
    DynamoDBConversationStore,
    ModelTokenBudget,
    OpenRouterModelAdapter,
    PromptContextAssembler,
    TokenCompactor,
)

budget = ModelTokenBudget(context_limit=1_050_000, response_tokens=4_096)

adapter = OpenRouterModelAdapter(
    api_key=openrouter_key,
    model_id="openai/gpt-5.6-luna",
    token_budget=budget,
    timeout_seconds=15,
    max_attempts=2,
)

store = DynamoDBConversationStore(
    boto3.client("dynamodb", region_name="ap-northeast-2"),
    table_name=user_table_name,
)
assembler = PromptContextAssembler(adapter.count_input_tokens)
memory_reviewer = AutomaticMemoryReviewer(store, adapter.review_memory)
compactor = TokenCompactor(store, adapter.summarize)

orchestrator = ConversationOrchestrator(
    store=store,
    assembler=assembler,
    memory_reviewer=memory_reviewer,
    compactor=compactor,
    generate_answer=adapter.generate_answer,
    deliver=deliver,
    system_prompt=system_prompt,
    token_budget=budget,
    model_id=adapter.model_id,
    explicit_memory_failure_notice="이번에는 기억에 반영하지 못했어요.",
)
```

`deliver(user_key, text)` is an application-owned async callable. The consuming application must pass the
same `budget` object and `adapter.model_id`; do not maintain equivalent duplicate settings.

On shutdown:

```python
await adapter.aclose()
```

## Current validated reference

As of 2026-09-11, the following exact OpenRouter configuration passed all eight fixed synthetic smoke
scenarios without internal retry in about 19 seconds:

```text
model_id = openai/gpt-5.6-luna
context_limit = 1,050,000
response_tokens = 4,096
reasoning = not specified
```

This is integration evidence, not a hardcoded Harness default or permanent provider guarantee. Recheck
the current OpenRouter Models API, price, data policy, and smoke result before deployment. The intended
initial `pia` runtime setting is `timeout_seconds=15` and `max_attempts=2`.

## Live smoke tool

`scripts/smoke_openrouter_model.py` uses only public Adapter methods and eight fixed synthetic Korean
calls:

1. ordinary Answer -> `NONE`;
2. durable preference -> `UPDATE`;
3. targeted forget -> `FORGET`;
4. complete deletion request -> unconfirmed `DELETE_ALL`;
5. complete deletion confirmation -> confirmed `DELETE_ALL`;
6. Memory addition -> `REPLACE` with a meaningful change notice;
7. already-current Memory -> `UNCHANGED` with null text and no notice;
8. rolling Summary.

It rejects known moving/router aliases, continues after an individual failure, performs no internal
retry, and fails the aggregate if successful Answer/Summary results report multiple response model IDs.
Memory outputs do not expose a model ID, so model consistency is observable for six of eight calls.

The key is read only from `OPENROUTER_API_KEY`; it is never accepted in `argv`. Run live smoke only with
explicit approval and synthetic data. Exit codes:

- `0`: all eight scenarios pass and observed model is consistent;
- `1`: provider/model/Adapter mismatch;
- `2`: invalid local configuration.

Passing smoke does not authorize production deployment, guarantee future endpoint availability, or
replace `pia` integration tests.

## `pia` integration checklist

The consuming application must:

1. pin a reviewed `pia-harness` version or commit reproducibly;
2. load the API key from Secrets Manager and never log or persist it;
3. configure an exact model ID, its current context limit, response reserve, timeout, and attempts;
4. create the DynamoDB client/store with the correct table key and TTL contract;
5. derive one opaque stable user key from authenticated membership, never from mutable channel labels;
6. supply its trusted system prompt, localized explicit-Memory failure notice, and async channel delivery;
7. map Orchestrator statuses to user-facing channel behavior;
8. call `reset` and partition deletion from the application's reset and withdrawal workflows;
9. close the Adapter on shutdown;
10. run local integration tests before any AWS, Bot, or production change;
11. review cost limits and provider data handling before sending real user conversation content;
12. obtain separate approval for AWS/IAM, live user data, Bot cutover, and deployment.

The Harness contains no `pia` environment parser. Configuration names and deployment mechanics belong to
the consuming repository.
