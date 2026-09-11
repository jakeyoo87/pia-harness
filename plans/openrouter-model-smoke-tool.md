# OpenRouter Model Smoke Tool Plan

## Goal

Add a small, reusable live smoke tool to `pia-harness` so an operator can test one exact OpenRouter
model against the library's real `OpenRouterModelAdapter` before selecting it for `pia` integration.

The tool verifies provider compatibility and the agreed product contracts; it does not bypass the
Adapter with a second request implementation, modify configuration, store results, or deploy anything.
It uses only synthetic Korean conversation data.

Plan author and initial implementation Agent: Codex. Independent plan and implementation reviewer:
Claude. The owner may change either role; this branch remains the plan-to-implementation branch.

## Current state and motivation

`pia-harness/main@9132f99` contains the reviewed OpenRouter Model Adapter. Its automated tests use
`httpx.MockTransport` and prove local rendering, parsing, validation, cancellation, and safe errors, but
they cannot prove that a current OpenRouter endpoint accepts the exact parameters or reliably follows
the Memory semantics.

A one-off synthetic probe of `nvidia/nemotron-3-super-120b-a12b:free` found useful incompatibilities:

- the current answer and Summary requests receive HTTP 404 because the endpoint advertises and accepts
  `max_tokens`, not the Adapter's `max_completion_tokens`;
- after replacing that field only in the temporary probe, normal answer and Summary calls succeed;
- Memory Review succeeded on a later attempt but initially returned an unusable response, and output
  language varied between Korean and English.

Those findings are evidence for a later Adapter compatibility fix. This feature first turns the
one-off probe into a repeatable test so future model selection and regressions do not depend on ad-hoc
scripts. It does not fix those findings on this branch.

## Ownership boundary

The smoke tool belongs to `pia-harness` because it tests the reusable model Adapter's three
provider-calling contracts. It does not know about Telegram, members, portfolios, DynamoDB, AWS deployment, or
the running Bot.

`pia` or an operator supplies the API key at execution time. AWS Secrets Manager lookup remains outside
the Harness tool. The tool never accepts the API key as a command-line argument, because command lines
may be visible in process listings and shell history.

## Public shape

Add:

    scripts/smoke_openrouter_model.py

Invocation:

    OPENROUTER_API_KEY=... python scripts/smoke_openrouter_model.py \
      --model <exact-model-id> \
      --context-limit <tokens> \
      [--response-tokens 4096] \
      [--timeout-seconds 90]

Required inputs:

- `OPENROUTER_API_KEY` environment variable, read once and never printed;
- one exact model ID, never `openrouter/free`, `openrouter/auto`, or a moving `~...latest` alias;
- the context limit confirmed from OpenRouter's current Models API.

The tool constructs the public `ModelTokenBudget` and `OpenRouterModelAdapter`. It uses no private
Adapter method and no alternative raw HTTP path. A request rejected because the Adapter and endpoint
disagree is a test failure, not something the tool silently works around.

Keep the script runnable from a source checkout using documented `PYTHONPATH=src`, without adding a CLI
framework or console-entry-point packaging.

## Fixed synthetic scenarios

Use a fixed Korean system prompt and synthetic user data. No production or copied user data is accepted
as an argument.

Normal answer scenarios:

1. ordinary investment question -> expected `NONE`;
2. durable response preference -> expected `UPDATE`;
3. targeted forget request -> expected `FORGET`;
4. initial complete-deletion request -> expected `DELETE_ALL` with confirmation false;
5. affirmative response with an immediately preceding synthetic deletion-confirmation exchange ->
   expected `DELETE_ALL` with confirmation true.

Additional callables:

6. Memory Review with an empty Memory and the durable response preference -> expected `REPLACE`, a
   non-empty document within 4,000 characters, tuple change summary within existing bounds;
7. Memory Review with an already-current Memory and no new durable fact -> expected `UNCHANGED` with an
   explicit null `memory_text` and empty change summary;
8. rolling Summary over one synthetic Korean Turn -> expected non-empty Summary and valid optional
   provider completion-token count.

Eight calls per run remain below the existing free-model daily allowance for a small number of candidate
comparisons. Do not add repetition, load testing, concurrency testing, benchmarks, or statistical
scoring in this first tool.

## Result reporting

Print one compact JSON line per scenario followed by one aggregate JSON line.

Per-scenario fields:

- scenario name;
- `PASS` or `FAIL`;
- elapsed milliseconds;
- response model ID when available;
- expected and actual Memory action/confirmation where applicable;
- provider token counts when available;
- output text for these fixed synthetic cases so the operator can inspect language and quality;
- safe Adapter error event, status, and exception type on failure.

Aggregate fields:

- requested exact model ID;
- sorted distinct response model IDs observed across successful scenarios;
- total, passed, and failed counts;
- total elapsed milliseconds;
- overall `PASS` only when all scenarios pass and they report exactly one response model ID. More than
one observed model fails the aggregate even when every individual scenario otherwise passes.

`MemoryReviewOutput` intentionally has no response model field. Report null for the two Memory Review
scenarios and compute observed-model consistency from the five Answer results and the Summary result,
which expose model IDs through their public domain contracts. Do not change the Adapter contract or read
private client state solely for this diagnostic. The early moving/router alias rejection remains the
guard against intentionally variable routing.

Never print or serialize:

- the API key or Authorization header;
- provider response bodies from failed requests;
- exception causes or request objects;
- environment values other than the requested non-secret model and numeric limits.

Exit codes:

- `0`: every scenario passes;
- `1`: one or more model/Adapter scenarios fail;
- `2`: local usage or configuration error before live calls.

An individual provider failure does not stop later scenarios. This is not an automatic retry: each
scenario runs once, is reported once, and the suite continues so one run exposes the full compatibility
surface.

## Validation rules

- Reject a missing, empty, or whitespace-only API key without echoing it.
- Reject empty or known moving/router model IDs listed above. This check is only for the diagnostic CLI;
  runtime Adapter policy remains unchanged.
- Reject non-positive context limit, response tokens, and timeout.
- Refuse a response-token reserve that is not smaller than the context limit through
  `ModelTokenBudget`.
- Use only fixed synthetic prompts defined in the script; do not add arbitrary `--prompt` input.
- Preserve `asyncio.CancelledError` and keyboard interruption rather than converting them into model
  failures.

## Program structure

Keep implementation small:

- immutable scenario/result data classes only if they remove repetition;
- one async runner that owns and closes the Adapter;
- small helpers for answer contexts, Memory Review, Summary, safe result serialization, and CLI parsing;
- standard-library `argparse`, `asyncio`, `json`, and monotonic timing;
- existing `pia_harness` domain types and Adapter only.

Do not add a model registry, test database, result history, ranking algorithm, web UI, AWS client,
credential provider abstraction, or evaluation framework.

## Automated tests

Add `tests/test_openrouter_model_smoke.py` using an injected fake Adapter or patched factory. No live
network or credential in automated tests.

Required cases:

1. all eight passing scenarios produce eight result lines, one passing aggregate, and exit `0`;
2. wrong action or delete-confirmation flag fails only that scenario and exits `1`;
3. safe Adapter error is reported without aborting later scenarios;
4. Memory output bounds and Summary non-empty checks affect pass/fail correctly;
5. API key and synthetic Authorization-like sentinel never appear in output or exception text;
6. missing/empty key and invalid numeric arguments exit `2` without a live call;
7. known moving/router aliases are rejected before a live call;
8. an `UNCHANGED` Memory response with explicit null `memory_text` parses and passes;
9. differing response model IDs fail the aggregate and exit `1`;
10. Adapter `aclose()` occurs on pass, failure, and cancellation and releases both owned clients;
11. output contains only the documented fields and valid JSON lines;
12. the existing mocked Adapter and full Harness suites remain green.

After automated tests, one separately approved live invocation may use the existing EC2 secret through
an external wrapper that passes the value only in process memory. The script itself does not import
`pia`, boto3, or Secrets Manager code. Record no secret and commit no live output.

## Documentation

Update `README.md` with:

- purpose and non-production status;
- source-checkout invocation;
- eight-call cost/rate-limit warning;
- exact model/context requirement;
- environment-only secret rule;
- output and exit-code meaning;
- statement that passing smoke does not approve deployment or guarantee future free endpoint
  availability.

## Out of scope

- fixing `max_completion_tokens` compatibility or changing Adapter prompts;
- selecting or hardcoding a production model;
- automatic Models API discovery, prices, rate-limit lookup, or capability caching;
- arbitrary prompt testing, benchmarks, repeated sampling, scoring, ranking, or load testing;
- keyword-based production Memory detection;
- database, Memory persistence, Orchestrator delivery, Telegram, `pia-agent`, AWS, deployment, or Bot
  changes;
- CI live calls, stored API keys, `.env` files, or committed smoke output.

## Implementation order after review

1. Add the script with fixed scenarios, safe reporting, exit codes, and Adapter lifecycle.
2. Add mocked CLI/runner tests without network or secrets.
3. Update `README.md`.
4. Run focused tests, Ruff, and the full Harness suite once.
5. Commit and push implementation on this branch for Claude final review.
6. After merge, run the selected exact model through the tool using a separately approved live secret
   wrapper, then fix Adapter compatibility on a new branch using the recorded non-secret findings.

## Claude review questions

1. Is testing the public Adapter rather than duplicating raw OpenRouter HTTP the correct boundary?
2. Are eight fixed calls enough to cover MemoryAction, null Memory output, Memory Review, and Summary without becoming an
   evaluation framework?
3. Are environment-only CLI secret input plus an external EC2 Secrets Manager wrapper sufficient to
   keep AWS concerns out of Harness?
4. Do result fields expose enough compatibility evidence without leaking requests or provider bodies?
5. Are continuing after per-scenario failure and returning aggregate exit `1` appropriate without being
   an automatic retry?
6. Is rejecting moving/router aliases appropriate for this diagnostic tool while the runtime Adapter
   remains model-agnostic?
7. Is anything excessive for the first repeatable live model test, or is a critical scenario missing?

If a blocker exists, propose the smallest correction. Do not fix the Adapter on this branch, add raw
provider request code, arbitrary prompts, retries, scoring, a model registry, AWS access, database work,
CI live calls, `pia-agent` changes, or deployment.

## Resolution record: 2026-09-11, after Claude plan review

The review corrections are accepted. Add an eighth `UNCHANGED` Memory Review scenario that requires an
explicit null `memory_text`; collect successful response model IDs and fail the aggregate when more than
one distinct model appears; describe the three provider-calling Adapter contracts accurately; and always
finish the async runner with `aclose()` so both owned clients are released. The early moving-alias denylist
remains a cheap guard, while observed-model consistency is the general runtime check. Nothing else in the
scope changes, and implementation proceeds on this branch.

## Review record: 2026-09-11, Claude, plan commit be6a325

Plan-only review against merged `main` at `9132f99`. Nothing was implemented and nothing was merged. No
blocker. One scenario is missing, one rule is unenforceable as written and can be made observable
instead, and two wording points need correcting. The shape of the tool is right and needs no
restructuring.

What holds. Driving the public Adapter rather than a second HTTP path is the only boundary that proves
anything: a raw request would test the operator's understanding of OpenRouter, while this tests the code
that will actually run. Refusing to work around a rejected parameter is the point of the exercise, and
deferring the `max_completion_tokens` finding to a later branch keeps this feature a measurement rather
than a fix. Reading the key from the environment and never from `argv` is correct for the stated reason,
and leaving Secrets Manager outside the Harness keeps the same line every earlier feature drew. Running
each scenario once, continuing after a failure, and aggregating into one exit code is the right shape:
it exposes the whole compatibility surface in one run without becoming a retry policy, and because the
report distinguishes a safe Adapter error from an expected-versus-actual action, an operator can tell a
rejected parameter from a model that simply judged one case differently. Printing the generated text is
safe here precisely because every input is fixed synthetic Korean data.

## Missing scenario: no planned call can produce a null `memory_text`

The Memory schema declares `memory_text` as `{"type": ["string", "null"]}` and marks it required, so
`UNCHANGED` and `CLEAR` must both come back with an explicit JSON null. Strict structured-output
implementations do not all accept a union type, and that is exactly the kind of endpoint difference this
tool exists to find before integration.

Every planned scenario returns a string there. Scenario 6 expects `REPLACE`, and the five answer
scenarios use a different schema entirely. So a provider that cannot emit `null` under strict mode would
pass this smoke run and then fail in production on `UNCHANGED`, which is the most common automatic-review
outcome of all: every revisit that finds nothing worth remembering returns it.

Smallest correction: add one Memory Review scenario whose expected action is `UNCHANGED` with
`memory_text` null, by supplying a Memory document that already contains the preference and Turns that
add nothing new. Eight calls per run instead of seven does not change the rate-limit argument. If the
owner would rather also exercise `allow_clear`, a targeted-forget case expecting `CLEAR` covers the same
null field, but `UNCHANGED` is the more representative production path and is the better single addition.

## Make the moving-alias rule observable rather than a name list

Rejecting `openrouter/auto`, `openrouter/free` and `~...latest` is right, but a denylist cannot be
complete, and the failure it guards against is severe: a router alias makes each of the eight scenarios
potentially hit a different model, which quietly turns the whole report into noise.

Smallest correction, using data the Adapter already returns: record the response model ID for every
scenario, report the distinct values in the aggregate line, and fail the run when more than one appears.
That detects any routing alias, including names nobody thought to list, and it costs nothing because the
Adapter already reports the response model on success. Keep the name check as the cheap early guard it
is.

## Two wording corrections

The ownership section says the tool exercises the Adapter's four public callable contracts, but only
three involve a provider. `count_input_tokens` is local, already covered by the mocked suite, and
correctly absent from the scenarios; say three.

Cleanup must be `aclose()`, not `close()`. The Adapter creates both a sync and an async client when none
are injected, `close()` releases only the sync one, and the runner is async, so verification case 8
should name `aclose()` and assert both owned clients are released.

## Answers to the review questions

1. Correct, and the only boundary that proves the shipped code works.
2. Enough, once the null-`memory_text` case above is added; the ban on repetition, scoring and
   concurrency is what keeps this a smoke test rather than an evaluation platform.
3. Sufficient. The environment-only rule plus an external wrapper keeps AWS out of the Harness while
   still allowing one approved live run.
4. Yes. Safe event, status and exception type on failure, generated text only for fixed synthetic
   inputs, and no bodies, headers, causes or key.
5. Appropriate. One call per scenario with a per-scenario verdict is a measurement, not a retry, and the
   aggregate exit code is what an operator or a later script can act on.
6. Appropriate for a diagnostic CLI, and better still with the observed-model check above; the runtime
   Adapter stays model-agnostic either way.
7. Nothing is excessive. The ten test cases, three exit codes and field list are all proportionate, and
   the hedge that data classes appear only if they remove repetition is the right instinct. The one
   missing item is the scenario above.

## Instructions for Codex

Add the `UNCHANGED` scenario and the observed-model check, correct the two wording points, and add two
verification cases: a null `memory_text` response parses and passes, and a run whose scenarios report
different response model IDs fails. Nothing else needs to change, and no raw provider request, arbitrary
prompt, retry, scoring, model registry, AWS access, or deployment work belongs in this branch.
