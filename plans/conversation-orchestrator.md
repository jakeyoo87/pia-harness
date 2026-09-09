# Conversation Orchestrator Plan

## Goal

Connect the completed Session, Memory, Compaction, and Prompt Context components into one
provider-independent conversation lifecycle with interruption behavior suitable for messaging. A new
message may supersede an answer still being generated; the replacement generation sees every pending
user message. Durable state changes remain serialized per user and session.

This branch is plan-only until independent review. The current handoff assigns plan authorship to
Codex and plan review to Claude. Implementation continues on this branch after review; later role
assignments may change by explicit user instruction.

## Existing baseline

`main` provides:

- an active Session pointer and completed-Turn persistence after successful delivery;
- rolling Summary Compaction with a protected recent tail and boundary CAS;
- automatic and forced Memory Review with boundary CAS;
- explicit current-input Memory Review using a preassigned turn ID;
- a requirement that explicit Memory commits and completed-Turn appends be serialized by this
  Orchestrator;
- a typed Prompt Context Assembler with trust classification and counts-only overflow;
- shared 4,096 response-token reservation between assembly and Compaction.

All current model-facing components use injected callables. This feature preserves that boundary and
does not add OpenRouter, Nemotron, Telegram, AWS, or secrets.

## Product behavior

For one user, while answer A is still being generated:

    message A accepted
    -> generation A starts
    -> message B arrives
    -> generation A becomes superseded and is cancelled when possible
    -> A and B are combined as the current user input
    -> generation B starts from persisted Context plus both messages
    -> only generation B may be delivered and persisted

The replacement does not reuse hidden reasoning or partial assistant text. It restarts from the same
persisted Context plus all pending user messages. The resulting continuity comes from complete input,
not from storing chain-of-thought.

If a message arrives after a generation has entered its durable commit phase, the current response is
allowed to finish and the new message waits for the next generation. This linearization point prevents
delivery and persistence from being cancelled halfway through.

## Execution boundary

Work is divided into two classes.

Interruptible work:

- Prompt Context loading and assembly;
- answer generation through one cancellation-aware async callable;
- read-only provider work performed by that callable.

Serialized durable work:

- active Session pointer creation and reset;
- explicit and automatic Memory commits;
- completed-Turn append;
- Summary replacement and covered-Turn deletion;
- reset;
- delivery of the final response that defines the completed-Turn boundary.

Each user has a short-lived in-process coordination state and one durable-commit lock. The state lock
protects generation ownership and pending messages; it is never held across a model call. The commit
lock may span Memory Review, Compaction, final delivery, and Turn append for that user. Different users
remain concurrent.

This is a single-process contract for the current Bot architecture. Multi-process routing,
distributed locks, queues, leases, and recovery workers are non-goals. A future horizontally scaled
runtime must route one user's active session to one coordinator or introduce a separately reviewed
distributed mechanism.

## Accepted input

Add an immutable `ConversationInput` containing:

- opaque `user_key`;
- non-empty exact message text;
- timezone-aware `accepted_at`;
- one turn ID generated from that timestamp when accepted;
- optional explicit Memory mode supplied by a later intent detector: `NONE`, `REMEMBER_OR_CORRECT`, or
  `TARGETED_FORGET`.

The Orchestrator does not parse natural language. `TARGETED_FORGET` is the only mode that may call
`review_explicit_input(..., allow_clear=True)`. Complete Memory deletion remains a separately
confirmed direct command outside this feature.

Every accepted message keeps its own ID and timestamp while pending. The final answer batch uses the
newest pending message's ID and timestamp for the one completed Turn. Its stored `user_message` is a
deterministic concatenation of all pending message texts in acceptance order, using a fixed neutral
separator. The same combined text is passed to Prompt Context assembly.

Message boundary labels are untrusted-data presentation hints only. They do not create a trusted
instruction boundary and are never placed in the system prompt.

## Injected seams

The Orchestrator receives existing store, assembler, Memory reviewer, and Compactor instances plus:

- `generate_answer`, one cancellation-aware async callable accepting `AssembledPromptContext` and
  returning `GeneratedAnswer`;
- `deliver`, one async callable that delivers the final text and returns successfully only when the
  user received it.

`GeneratedAnswer` contains:

- non-empty final answer text;
- model ID;
- optional matching provider `ContextUsage` for post-generation Compaction pressure.

The answer callable must not perform state-changing tools in this feature. Read-only research may be
cancelled. Risk Check, order approval, broker execution, and any other irreversible action require a
later non-interruptible action phase and are excluded.

Existing synchronous Memory and Compaction calls may run through executor tasks so the event loop can
accept a newer message. Once durable work starts it is shielded from task cancellation and serialized
under the user's commit lock; its result is accepted only for the generation or explicit input it
belongs to.

## Per-user coordination state

Maintain only in-process state while work is active:

- monotonically increasing `generation_id`;
- ordered pending inputs not yet represented by a delivered completed Turn;
- active generation task and cancellation handle;
- phase: `GENERATING` or `COMMITTING`;
- successful Memory change-summary items waiting to be attached to the next final response.

No pending prompt, partial output, hidden reasoning, or change-summary outbox is persisted. Process
restart loses in-flight work; completed durable writes remain valid and the messaging channel may
redeliver or the user may retry.

## Interruption algorithm

When a message arrives:

1. create and validate its `ConversationInput`;
2. under the short state lock, append it to that user's pending inputs;
3. if the phase is `GENERATING`, increment `generation_id`, mark the previous generation superseded,
   request cancellation, and start a replacement using the full pending input batch;
4. if the phase is `COMMITTING`, queue it without cancelling the current delivery or durable commit;
5. after the current commit finishes, start the next generation for queued inputs.

Cancellation is best effort. Correctness comes from generation ownership, not provider cancellation:
before delivery or any generation-owned persistence, the task must confirm its ID is still current. A
late result from a superseded task is discarded even if the provider ignored cancellation.

The superseded caller receives a structured `SUPERSEDED` result and must not send another response.
Only the caller owning the newest generation can receive a delivered result.

## Generation flow

For the current pending batch:

1. briefly acquire the user commit lock to get or create the active Session, then release it;
2. load current Memory and `load_context` for that Session;
3. combine pending messages and assemble Prompt Context;
4. if assembly overflows, enter the bounded overflow flow below;
5. call `generate_answer` without holding the state or commit lock;
6. if superseded, discard the answer and stop;
7. atomically claim the `COMMITTING` phase under the state lock;
8. under the per-user commit lock, run due Memory work, optional Compaction, delivery, and completed-Turn
   append in the order below;
9. clear only the inputs covered by the successfully delivered Turn;
10. if later inputs queued during commit, start their generation; otherwise remove idle coordinator
    state.

## Commit-phase order

After a generation becomes the current commit owner:

1. run a due one-hour revisit Review over earlier persisted Turns;
2. process pending explicit Memory inputs in acceptance order using each input's own ID and timestamp;
3. preserve successful change summaries in acceptance order, deduplicated only by exact text;
4. if post-generation Compaction is due, force Review of any remaining persisted unreviewed Turns;
5. run Compaction against the generated answer's model ID and usage;
6. append successful meaningful Memory changes to the bottom of the final answer, at most three items
   overall;
7. call the injected delivery callable once;
8. only after delivery succeeds, append one completed Turn using the combined pending input, final
   delivered answer, and newest pending input's ID and timestamp.

Memory Review or Compaction failure is recorded in the structured result but does not block answer
delivery. Failed explicit Memory Review must not produce a success notice. Delivery failure prevents
completed-Turn append but does not roll back already committed explicit Memory, automatic Memory, or
Compaction changes.

A new message arriving after step 7's commit ownership was claimed is queued for the next generation;
it does not interrupt delivery or append. The state-commit lock therefore closes the explicit Memory
read-to-write race documented by the preceding feature.

## Context overflow flow

`ContextBudgetExceeded` is handled at most once per generation:

1. confirm the generation is still current;
2. acquire the user commit lock;
3. force Memory Review of persisted unreviewed Turns, without allowing CLEAR;
4. call Compaction with the overflow's `required_input_tokens` as `estimated_context_tokens` and with
   `usage=None`;
5. if Compaction reports no progress or fails, return structured `CONTEXT_OVERFLOW` and stop;
6. reload Memory and Conversation Context and reassemble exactly once;
7. if assembly still overflows, return `CONTEXT_OVERFLOW`; otherwise continue generation.

There is no unbounded retry. The Orchestrator never truncates Memory, Summary, Turns, or the current
input. User-facing overflow wording remains a later `pia-agent` concern.

## Memory and interruption

Explicit Memory Review does not start during an interruptible generation. It starts only after the
latest generation claims commit ownership, so a superseded batch cannot commit a new explicit change.
All explicit inputs in the winning batch are processed in their original order.

Once an explicit Memory update commits, interruption is no longer allowed for that response; newer
messages queue. Its change summary can therefore be attached to the same final answer without a
durable notification outbox. If delivery fails, the Memory update remains and the summary may be lost;
this is the already accepted best-effort notification boundary.

Automatic revisit Review reads only earlier persisted Turns. It is performed in the same commit phase
rather than concurrently with answer generation in this first Orchestrator, trading some latency for
a single simple serialization boundary. Parallel Review can be reconsidered after the lifecycle is
proven and measured.

## Reset boundary

Add a separate orchestrated reset operation:

1. supersede and cancel any interruptible generation for the user;
2. acquire commit ownership and the user commit lock;
3. attempt best-effort Memory Review of persisted unreviewed Turns;
4. reset the active Session regardless of Review success;
5. clear in-process pending inputs and change summaries for the old Session;
6. return the new Session or a structured failure.

Reset does not interrupt delivery or another commit already in progress; it waits for that commit,
then runs. Long-term Memory remains; the old rolling Summary is removed by the existing store method.

## Structured outcomes

Return a small result with one status:

- `DELIVERED`: final response delivered and completed Turn persisted;
- `SUPERSEDED`: this invocation was replaced by a newer pending batch;
- `CONTEXT_OVERFLOW`: one allowed Compaction/reassembly attempt could not make the prompt fit;
- `GENERATION_FAILED`: answer callable failed before commit ownership;
- `DELIVERY_FAILED`: final delivery failed and no completed Turn was appended;
- `PERSISTENCE_FAILED`: delivery succeeded but completed-Turn append failed.

The result may contain final text only for the current delivered or delivery/persistence-failed
generation, plus non-sensitive error classification and Memory/Compaction failure flags. It never
contains hidden reasoning, partial output, Memory text, credentials, or raw provider errors.

## Failure and recovery

- Provider cancellation failure: late generation result is discarded by generation ID.
- Generation failure: pending inputs remain available for a later accepted message or explicit retry;
  nothing is persisted.
- Memory failure: answer proceeds without a Memory success notice.
- Compaction failure: answer proceeds when generation already succeeded; pre-generation overflow stops.
- Delivery failure: no completed Turn is stored; committed Memory or Compaction is not rolled back.
- Turn append failure after delivery: return `PERSISTENCE_FAILED` with the stable turn ID so the caller
  may invoke an idempotent append retry; do not regenerate or redeliver automatically.
- Process restart: in-flight state is lost; no recovery worker or outbox is introduced.
- Different users never share coordinator state, locks, pending input, Context, or results.

## Minimal API surface

- `ConversationInput` and explicit Memory mode;
- `GeneratedAnswer`;
- Orchestrator status and result;
- `ConversationOrchestrator.submit` and `reset` async methods;
- one internal per-user coordinator with generation ownership and commit serialization;
- injected async answer and delivery callables.

No provider gateway, Telegram adapter, persistent queue, distributed lock, outbox, retry worker, tool
executor, workflow framework, or generalized event bus is added.

## Scope

1. Provider- and channel-independent Orchestrator.
2. Hermes-style pending-input interruption and late-result suppression.
3. Per-user/session durable state-commit serialization.
4. Integration of existing Session, Memory, Context Assembler, and Compaction contracts.
5. Explicit Memory change-summary attachment to the same delivered answer.
6. Bounded overflow Compaction and reassembly.
7. Orchestrated reset.
8. Deterministic async tests with fake answer, review, summary, token, and delivery callables.
9. README and this plan update.

## Non-goals

- Actual OpenRouter, Nemotron, or tokenizer integration.
- Natural-language Memory intent detection.
- Telegram, `pia-agent`, AWS, IAM, Secrets Manager, or deployment changes.
- Streaming partial messages or editing an already visible partial response.
- State-changing tools, Risk Check, approval, or broker execution.
- Multi-process coordination, distributed locks, queues, workers, or durable in-flight recovery.
- Automatic retries beyond the single overflow Compaction/reassembly attempt.

## Focused verification

1. A second message during generation supersedes the first, cancels best effort, combines both inputs,
   delivers once, and persists exactly one final completed Turn.
2. A provider that ignores cancellation cannot deliver or persist a late superseded result.
3. A message arriving after commit ownership queues for the next generation rather than interrupting
   delivery or persistence.
4. Different users generate and commit independently.
5. Explicit Memory inputs are reviewed in acceptance order under the commit lock; targeted forget is
   the only CLEAR path and successful changes appear in the same response.
6. No Turn append can occur between explicit Memory's final persisted-Turn reload and Memory write
   through the Orchestrator path.
7. Revisit and pre-Compaction Memory failure do not block delivery; failed Review emits no success
   notice.
8. Exact Context overflow runs at most one Compaction and one reassembly, passes the assembler total
   with no provider usage, and stops when Compaction makes no progress.
9. Delivery failure writes no completed Turn; append failure after delivery returns the stable ID for
   idempotent retry without redelivery.
10. Reset waits for an active commit, cancels only interruptible generation, preserves Memory, and
    switches the Session.
11. Idle coordinator state is removed and pending data never crosses users.
12. Existing Session, Compaction, Memory, and Assembler tests remain green.

## Questions for independent review

Please identify only concrete blockers, material omissions, or unnecessary scope:

1. Is the linearization point between `GENERATING` and `COMMITTING` sufficient to decide whether a new
   message interrupts or queues?
2. Can state-lock generation ownership plus a separate per-user commit lock guarantee that no stale
   answer, Turn, Memory boundary, Compaction result, or reset commits out of order?
3. Is delaying automatic and explicit Memory Review until the winning commit phase the simplest way to
   preserve interruption and same-response change notices?
4. Is combining pending messages into one completed Turn under the newest input ID compatible with
   Session, Memory boundary, and later transcript behavior?
5. Are the injected async answer and delivery callables sufficient without introducing a provider or
   channel abstraction?
6. Does wrapping current synchronous Memory and Compaction work in shielded executor tasks preserve
   responsiveness without allowing a cancelled generation to commit?
7. Are delivery-before-Turn-persistence and non-rollback of prior Memory/Compaction still coherent in
   every interruption and failure path?
8. Is one bounded overflow Compaction/reassembly attempt enough, and does the `usage=None` rule avoid
   the known trigger-precedence failure?
9. Does the reset sequence wait at the right boundary without reviving superseded pending input?
10. Is any state, status, callable, validation, or test unnecessary for this first Orchestrator?

If a blocker exists, propose the smallest correction. Do not add a provider adapter, Telegram code,
streaming UI, state-changing tools, distributed coordination, queue, worker, outbox, or AWS work.
