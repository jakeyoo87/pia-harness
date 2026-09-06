# pia-harness

Minimal, reusable conversation and memory harness for PIA. The first feature stores completed,
user-isolated conversation turns in DynamoDB. Model calls, compaction, and long-term memory are
deliberately added as separate features.

## Conversation session core

- One active-session pointer per opaque user key
- One DynamoDB item per completed user/assistant turn
- Fourteen-day configurable raw-turn retention
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

## Local verification

Start DynamoDB Local, install the package, and run:

    PIA_HARNESS_DYNAMODB_ENDPOINT=http://127.0.0.1:8000 \
      python -m unittest discover -s tests -v
