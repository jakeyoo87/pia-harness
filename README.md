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
- Reserves the configured 4,096-token response limit before calculating the trigger
- Prefers provider total-token usage and uses conservative estimation as a fallback
- Keeps the newest turn and a token-budgeted recent tail verbatim
- Stores one rolling summary per session before deleting covered raw turns
- Loads context through one boundary-aware path to prevent summary/turn duplication
- Removes the session summary on reset and all data on account closure

Model network integration is intentionally separate. The compactor accepts one summary callable;
OpenRouter and Nemotron will be connected in a later feature.

See [the compaction plan](plans/token-compaction.md) for the exact contracts.

## Automatic long-term Memory

- One free-form Memory document of at most 4,000 characters per opaque user key
- Reviews earlier unreviewed Turns when the user returns after a one-hour gap
- Supports forced remember, correction, and forget Review without parsing commands in the harness
- Allows CLEAR only when the caller explicitly marks a forced Review as a forget operation
- Uses a conditional review boundary so stale results cannot overwrite newer Memory
- Returns up to three short change-summary items only after a successful write
- Preserves Memory on reset and deletes it with the user's partition on account closure

The reviewer is one injected callable. Response generation, user-facing notices, OpenRouter/Nemotron,
and `pia-agent` integration remain separate.

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
- Reserves the same configurable response budget used by Compaction
- Returns a counts-only `ContextBudgetExceeded` instead of truncating or compacting content

Model rendering, Compaction retry, provider calls, and user-facing overflow behavior remain later
adapter and Orchestrator responsibilities.

See [the Context Assembler plan](plans/prompt-context-assembler.md) for the exact contracts.

## Local verification

Start DynamoDB Local, install the package, and run:

    PIA_HARNESS_DYNAMODB_ENDPOINT=http://127.0.0.1:8000 \
      python -m unittest discover -s tests -v
