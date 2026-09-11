# pia-harness

Minimal, reusable conversation and memory harness for PIA. The first feature stores completed,
user-isolated conversation turns in DynamoDB. Model calls, compaction, and long-term memory are
deliberately added as separate features.

## Conversation session core

- One active-session pointer per opaque user key
- One DynamoDB item per completed user/assistant turn
- Thirty-day configurable raw-turn retention
- Context reset by replacing the active session ID
- Immediate, partition-scoped deletion on account closure
- No S3 archive, secondary index, session-history state, or background worker

See [the feature plan](plans/conversation-session-core.md) for the exact contracts.

## Token-based compaction

- Triggers after a response at 90% of the model's usable input budget
- Uses one shared model token budget and reserves its default 4,096-token response limit before the
  trigger
- Prefers provider total-token usage and uses conservative estimation as a fallback
- Keeps the newest turn and a token-budgeted recent tail verbatim
- Stores one rolling summary per session before deleting covered raw turns
- Loads context through one boundary-aware path to prevent summary/turn duplication
- Removes the session summary on reset and all data on account closure

The compactor accepts one summary callable; the OpenRouter adapter now supplies it for any compatible,
caller-selected exact model.

See [the compaction plan](plans/token-compaction.md) for the exact contracts.

## Automatic long-term Memory

- One free-form Memory document of at most 4,000 characters per opaque user key
- Reviews earlier unreviewed Turns when the user returns after a one-hour gap
- Supports natural-language remember, correction, and forget actions returned with the normal generated
  answer, without parsing commands or making a separate intent-model call in the harness
- Allows CLEAR only when the caller explicitly marks a forced Review as a forget operation
- Uses a conditional review boundary so stale results cannot overwrite newer Memory
- Returns up to three short change-summary items only after a successful write
- Preserves Memory on reset and deletes it with the user's partition on account closure

The reviewer is one injected callable shared by automatic and explicit Review. The OpenRouter adapter
now supplies it; `pia-agent` integration remains separate.

Explicit remember, correction, and targeted-forget callers may also pass the accepted current user
input before its completed Turn exists. The Memory boundary advances to the input's preassigned turn
ID, CLEAR remains available only on that explicit path, and the later completed Turn reuses the same
ID. The later Orchestrator must serialize same-user/session state commits; the harness also rejects a
stale result when the persisted-Turn set changes during Review.

See [the automatic Memory plan](plans/automatic-long-term-memory.md) for the exact contracts.
See [the explicit current-input plan](plans/explicit-memory-current-input.md) for this follow-up.

## Prompt and Context assembly

- Produces an ordered, provider-independent list of typed Context parts
- Keeps the system prompt as the only trusted instruction
- Marks Memory, Summary, historical messages, and the current request as untrusted data
- Validates user, session, Turn order, and Summary boundaries before model input is built
- Uses one adapter-supplied counter for the complete rendered request, including tools and framing
- Uses the same immutable model token budget as Compaction
- Returns a counts-only `ContextBudgetExceeded` instead of truncating or compacting content

Model rendering, Compaction retry, provider calls, and user-facing overflow behavior remain later
adapter and Orchestrator responsibilities.

See [the Context Assembler plan](plans/prompt-context-assembler.md) for the exact contracts.

## Conversation orchestration

- A newer message supersedes an answer still being generated and restarts with all pending input
- Generation IDs discard late results even when provider cancellation is ignored
- Once a response claims commit ownership, newer messages queue for the next response
- Session, Memory, Compaction, delivery, and completed-Turn commits are serialized per user
- Different users continue independently
- Explicit Memory updates run only for the winning response and their changes appear in that response
- Generated `UPDATE` and `FORGET` actions reuse the existing explicit-input reviewer; the caller no
  longer classifies inputs before answer generation
- The caller supplies one required, bounded failure notice so a failed explicit update cannot be
  delivered as an apparent success
- Complete Memory clearing requires a preceding delivered confirmation request and writes an empty
  document at the newest boundary so retained Turns cannot rebuild deleted Memory
- Context overflow permits one Compaction and one reassembly attempt without truncation
- One model token budget supplies both context limit and response reserve to every stage
- Delivery success clears covered pending input even if completed-Turn persistence later fails

The implementation is single-process and provider/channel independent. Model adapters, natural-language
Memory intent detection, Telegram, state-changing tools, distributed coordination, and durable in-flight
recovery remain separate.

See [the Orchestrator plan](plans/conversation-orchestrator.md) for the exact lifecycle.

## OpenRouter model adapter

- Calls one caller-selected exact OpenRouter model for normal answers, Memory Review, and Summary
- Returns `answer` and the hidden `MemoryAction` from the same structured answer call
- Provides the one Memory reviewer callable shared by automatic and explicit Review
- Uses strict JSON Schema, required-parameter routing, and local fail-closed validation
- Uses OpenRouter's broadly supported `max_tokens` field for capped Answer and Summary requests; Memory
  Review remains bounded by its 4,000-character schema and domain validation without a token cap
- Preserves the Assembler trust boundary when rendering Memory, Summary, and conversation data
- Defines `UNCHANGED`, `REPLACE`, and `CLEAR` output semantics explicitly and asks Memory changes and
  rolling Summary to preserve the source conversation's primary language
- Uses provider token usage when available and conservative preflight estimates otherwise
- Accepts `max_attempts=1` or `2`; the default performs one call, while `2` retries a narrowly classified
  transient provider/structured-output failure once after one second
- Keeps answer generation async and cancellable while Review and Summary match their synchronous durable
  callable contracts
- Never performs a separate intent call, keyword parse, retry, model fallback, or live capability probe

Construct `OpenRouterModelAdapter` with an injected API key, exact model ID, shared `ModelTokenBudget`,
timeout, optional `max_attempts`, and optional test clients. Wire `count_input_tokens`, `generate_answer`, `review_memory`, and
`summarize` directly into the existing Harness components. Call `aclose()` to close all adapter-owned
clients; injected clients remain caller-owned.

Use an exact deployed model ID whose context window matches the supplied budget. Do not use a routing
alias whose effective model and context limit can change. The consuming `pia` application owns Secret
Manager access, runtime configuration, DynamoDB/IAM, Telegram delivery, deployment, and live E2E tests.
With `max_tokens`, the response reserve covers reasoning plus final output together. A
`finish_reason="length"` response is reported as non-retryable truncation. When `max_attempts=2`, choose a
timeout with the two-timeout-plus-one-second worst case in mind; the smoke tool intentionally keeps the
default one attempt so it measures raw endpoint behavior.

See [the Model Adapter plan](plans/openrouter-model-adapter.md) for request and validation contracts.

## Live model smoke tool

Before selecting an exact OpenRouter model for `pia`, run the repository-local smoke tool against the
public Adapter. It sends eight fixed synthetic Korean requests covering every `MemoryAction`, complete
deletion confirmation, `REPLACE` and null-valued `UNCHANGED` Memory Review, and rolling Summary.

From a protected source checkout where the key is already present in the process environment:

    PYTHONPATH=src python scripts/smoke_openrouter_model.py \
      --model vendor/exact-model-id \
      --context-limit 262144

The tool reads only `OPENROUTER_API_KEY`; never pass a key as an argument, paste it into documentation,
or commit it in `.env`. On AWS, use an external owner-controlled wrapper to load Secrets Manager data
and invoke the tool in the same process. The Harness script itself contains no AWS integration.

Each scenario emits one JSON line with status, elapsed time, safe model/usage metadata, and synthetic
output text, followed by an aggregate line. Exit `0` means all eight contracts passed with one observed
response model, `1` means a model or Adapter mismatch, and `2` means invalid local configuration.
Failures are not retried. One run consumes eight model calls, and a pass neither approves deployment nor
guarantees that a free endpoint will remain available.

See [the smoke tool plan](plans/openrouter-model-smoke-tool.md) for the fixed scenarios and safety
boundary.

## Local verification

Start DynamoDB Local, install the package, and run:

    PIA_HARNESS_DYNAMODB_ENDPOINT=http://127.0.0.1:8000 \
      python -m unittest discover -s tests -v
