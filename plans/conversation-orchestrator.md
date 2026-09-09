# Conversation Orchestrator Plan

## Goal

Connect the completed Session, Memory, Compaction, and Prompt Context components into one
provider-independent conversation lifecycle with interruption behavior suitable for messaging. A new
message may supersede an answer still being generated; the replacement generation sees every pending
user message. Durable state changes remain serialized per user and session.

This plan was independently reviewed before implementation. Implementation continues on this branch;
later role assignments may change by explicit user instruction.

## Existing baseline

`main` provides:

- an active Session pointer and completed-Turn persistence after successful delivery;
- rolling Summary Compaction with a protected recent tail and boundary CAS;
- automatic and forced Memory Review with boundary CAS;
- explicit current-input Memory Review using a preassigned turn ID;
- a requirement that explicit Memory commits and completed-Turn appends be serialized by this
  Orchestrator;
- a typed Prompt Context Assembler with trust classification and counts-only overflow;
- one immutable `ModelTokenBudget` carrying context limit and the default 4,096 response-token reserve
  for assembly, Compaction, and Orchestration.

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

When the newest pending input is explicit, its current-input Memory Review uses the complete combined
pending text that the final Turn will store, while keeping the newest input's ID and timestamp. Earlier
explicit inputs keep their own text, ID, and timestamp. A Memory boundary therefore never advances to
the final Turn ID without the reviewer seeing all user text stored under that ID.

## Injected seams

The Orchestrator receives existing store, assembler, Memory reviewer, and Compactor instances plus:

- `generate_answer`, one cancellation-aware async callable accepting `AssembledPromptContext` and
  returning `GeneratedAnswer`;
- `deliver`, one async callable that delivers the final text and returns successfully only when the
  user received it.

It receives one `ModelTokenBudget` and passes that same object to the Assembler and Compactor. There
are no separate Orchestrator, Assembler, or Compaction response-budget variables to compare or keep in
sync. `CompactionPolicy` retains only ratios.

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

Add one read-only `TokenCompactor.should_compact` proxy over its existing policy so the Orchestrator can
decide whether pre-Compaction Memory flush is needed before calling the mutating compaction method. It
does not add another policy or trigger.

## Per-user coordination state

Maintain only in-process state while work is active:

- monotonically increasing `generation_id`;
- ordered pending inputs not yet represented by a delivered completed Turn;
- active generation task and cancellation handle;
- phase: `GENERATING` or `COMMITTING`;
- successful Memory change-summary items waiting to be attached to the current winning response.

No pending prompt, partial output, hidden reasoning, or change-summary outbox is persisted. Process
restart loses in-flight work; completed durable writes remain valid and the messaging channel may
redeliver or the user may retry.

Change summaries are commit-local. They are removed after successful delivery and discarded after
delivery failure; they are never carried into a later response. This deliberately avoids an outbox or
cross-response notification recovery in the first Orchestrator.

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
9. clear inputs covered by the response immediately after delivery succeeds, regardless of whether
   completed-Turn append succeeds;
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

If completed-Turn append fails after successful delivery, the covered pending inputs remain cleared so
the user is not answered twice. Return `PERSISTENCE_FAILED`; no automatic append retry, retry payload,
or durable recovery record is added in this Beta scope.

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
6. resolve every submit discarded by reset as `SUPERSEDED`;
7. return the new Session or a structured failure.

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
- Turn append failure after delivery: clear the delivered inputs and return `PERSISTENCE_FAILED`; do
  not regenerate, redeliver, create a retry payload, or retain recovery state.
- Process restart: in-flight state is lost; no recovery worker or outbox is introduced.
- Different users never share coordinator state, locks, pending input, Context, or results.

## Minimal API surface

- `ConversationInput` and explicit Memory mode;
- shared `ModelTokenBudget` with context and response token counts;
- `GeneratedAnswer`;
- Orchestrator status and result;
- `ConversationOrchestrator.submit` and `reset` async methods;
- read-only `TokenCompactor.should_compact` using the existing policy;
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
10. Replacement of separate context/response arguments with one shared immutable token budget.

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
9. Delivery failure writes no completed Turn and discards commit-local change summaries; append failure
   after delivery clears the covered inputs and returns `PERSISTENCE_FAILED` without retry state.
10. Reset waits for an active commit, cancels only interruptible generation, preserves Memory, and
    switches the Session; discarded submits resolve as `SUPERSEDED`.
11. Idle coordinator state is removed and pending data never crosses users.
12. Existing Session, Compaction, Memory, and Assembler tests remain green.
13. Assembler, Compactor, and Orchestrator receive the same `ModelTokenBudget`; no independent response
    reserve remains in their APIs or policy.

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

## Review record: 2026-09-09, Claude, plan commit d39f12e

Plan-only review against the merged Session, Compaction, Memory, explicit current-input, and Assembler
contracts. Nothing was implemented and nothing was merged. One blocker and two smaller corrections
follow. The concurrency design itself is sound and needs no structural change.

What holds. Claiming `COMMITTING` under the same state lock that decides interrupt-versus-queue closes
the window that question 1 asks about: there is no instant where a message can both see `GENERATING`
and arrive after commit ownership was taken. The two locks are not redundant, because they answer
different questions — the phase decides whether a new message interrupts, and the commit lock is what
`reset` waits on when it arrives from outside the generation flow. Generation ownership rather than
provider cancellation as the correctness mechanism is the right call, and it is what makes best-effort
cancellation acceptable. The commit lock also delivers exactly what the explicit Memory feature asked
for: no Turn append can land between that feature's final persisted-Turn reload and its Memory write,
because both happen inside one commit under the same lock. Running explicit Review only after commit
ownership is claimed is likewise correct — a superseded batch can never commit a Memory change.

Two integration details are also right and worth recording because they were easy to get wrong. The
overflow flow passes the assembler's own total as `estimated_context_tokens` with `usage=None`, which
avoids `should_compact` preferring the previous response's smaller provider usage and declining the
Compaction that assembly just demanded. And running the pre-Compaction force Review before Compaction
deletes covered Turns keeps unreviewed content from being deleted before Memory ever sees it.

## Blocker: an ordinary pending message is lost to Memory when the newest pending message is explicit

The plan stores one completed Turn under the newest pending input's ID with the combined text of every
pending message, and separately reviews each explicit input using its own ID and its own text. When the
newest pending input is the explicit one, those two rules collide.

Take a batch of message A, an ordinary question, and message B, an explicit remember request that
arrived while A was still being answered. This is an ordinary interaction, not a corner case: a user
asks something and then adds "and remember that I prefer long-term positions" before the answer lands.
Commit runs `review_explicit_input` for B with B's own text, which advances the Memory boundary to B's
turn ID. The completed Turn is then appended under that same ID with A's text, B's text and the answer.
`load_unreviewed_turns` filters on a strictly greater turn ID, so that Turn is excluded from every
later Review. A's text and the assistant's answer were never sent to any reviewer and never will be.

Without interruption the same message A would have become its own Turn and been reviewed on the next
revisit, so interruption silently reduces Memory coverage, and nothing reports it. The reverse ordering
is safe: when the explicit input is not the newest, the boundary stops below the combined Turn's ID and
that Turn stays eligible.

Smallest correction: make the explicit Review that shares the completed Turn's ID present the same text
that Turn will store.

    The explicit input whose turn ID equals the newest pending input's ID is reviewed with the combined
    pending text, not with its own message alone. Explicit inputs that are not the newest keep their own
    ID and their own text.

The boundary then only passes content the reviewer actually saw, which is the invariant the rest of the
Memory design rests on. It also makes the recovery path consistent: `review_explicit_input` compares a
persisted Turn carrying the current ID against the current input's `user_message`, and after this change
the two are the same combined string, so a Review attempted after the append still validates.

## Smaller corrections

1. Say that pending inputs are cleared on delivery success, not on completed-Turn persistence. Step 9
   currently reads "clear only the inputs covered by the successfully delivered Turn", which conflates
   the two. If an append failure left the inputs pending, the next batch would re-include a question the
   user has already been answered, produce a second answer to it, and store both texts in one Turn. The
   user has received the answer once delivery succeeds; a failed append should leave that exchange out
   of stored history rather than cause it to be answered again.

2. Give the submits that reset discards a defined outcome. Reset clears pending inputs for the old
   Session, but the status list has no result for a `submit` whose input is cleared that way, and those
   callers are left awaiting a response that will never be generated. Return `SUPERSEDED` for them: it
   already means "this invocation was replaced and must not send a response", which is exactly the
   situation, and it avoids adding a status.

## Answers to the review questions

1. Sufficient, because the phase claim and the interrupt-or-queue decision share one lock.
2. Yes. Ownership by generation ID suppresses late results, and the commit lock orders everything
   durable, including reset arriving from outside the generation flow.
3. Yes, and this is the simplest arrangement that also lets a change summary ride the same answer
   without a notification outbox.
4. Compatible for the Turn, the Session and the assembler, but not for the Memory boundary as written;
   see the blocker.
5. No conflict. Durable work begins only after commit ownership, which is the point at which
   interruption already stops, so shielding never protects a cancelled generation. An executor thread
   cannot be cancelled anyway, so the shield concerns the awaiting task only.
6. Coherent, with correction 1 applied. Delivery is the point where the user has the answer, so it is
   the right boundary for clearing pending state and for refusing to roll Memory back.
7. Enough. One Compaction plus one reassembly terminates, and stopping when Compaction reports no
   progress is the condition that makes it terminate rather than loop.
8. Correct at the boundary, with correction 2 for the discarded submits.
9. No unnecessary state, status or lock. One field's lifetime is unclear: the per-user state holds
   change summaries "waiting to be attached to the next final response", while the failure section says
   a summary may simply be lost when delivery fails. Say which it is, so an implementer does not build a
   cross-response carry-over that the rest of the plan does not want.

## Instructions for Codex

Apply the blocker's one-sentence rule and the two corrections before implementing. Add one verification
item for the blocker: a batch whose newest input is explicit must leave no stored Turn below or at the
Memory boundary whose text never reached a reviewer. Nothing else in the plan needs to change, and no
provider adapter, queue, outbox or distributed lock should appear during implementation.

## Resolution record: 2026-09-09, after Claude plan review

The blocker is adopted: the newest explicit input is reviewed with the exact combined pending text
that will be stored under its ID. Pending inputs clear on delivery success, and reset resolves discarded
submits as SUPERSEDED. The owner chose the simpler Beta failure boundary: Memory change summaries are
commit-local and discarded after delivery failure, and a completed-Turn append failure returns
PERSISTENCE_FAILED without an automatic retry, retry payload, outbox, or cross-response notification.
Implementation proceeds on this branch with those limits.

## Review record: 2026-09-09, Claude, implementation commit da423ee

Implementation review against this plan and the merged Session, Memory, Compaction and Assembler
contracts. Verdict: no blocker remains. Four defects were found and fixed on this branch; the branch
may be merged to main once the assigned agent confirms main CI succeeds.

Verified by running. The full suite is 42 tests green against DynamoDB Local, and ruff reports no
unused imports, no undefined names and no bugbear findings after the fixes below. Before them ruff
reported three errors, so the branch was not lint-clean as merged features have been.

Concurrency was checked by stress, not only by reading. Twenty-five concurrent submissions for one user
produced twenty-four `SUPERSEDED` results and one `DELIVERED`, with zero overlapping deliveries, exactly
one stored Turn, and every one of the twenty-five message texts present in that Turn's combined text.
A separate watcher observed exactly one coordinator object for that user across the run and confirmed
the state is removed once idle, so the per-user coordinator is neither duplicated nor leaked.

Lock ordering is sound. Two nestings exist — `commit_lock` then `state_lock` in the overflow path's
currency check, and `_states_lock` then `state_lock` in idle cleanup — and no path ever takes them in
the opposite order, because `_finish_generation` and `reset` both leave their `state_lock` block before
calling cleanup, and `submit` never reaches for the commit lock. There is therefore no cycle and no
deadlock.

The plan's own correctness claims hold in the code. Cancellation is requested only while the phase is
`GENERATING`, and once `_claim_commit` flips the phase under the state lock no caller cancels the task,
so the commit sequence cannot be interrupted halfway. A message arriving during `COMMITTING` appends to
pending without incrementing the generation counter, so `_finish_generation` still matches its own
generation and starts the queued work afterwards. Explicit Memory Review, Compaction, delivery and the
completed-Turn append all run inside one `commit_lock` acquisition, which is exactly the serialization
the explicit Memory feature asked for: no Turn append can interleave between that feature's final
reload and its Memory write. The blocker from the plan review is implemented correctly — the newest
input's explicit Review receives the combined text while earlier explicit inputs keep their own — and
removing that rule fails the suite.

## Defects found and fixed

1. A context overflow stranded the user permanently. Pending inputs were retained on
   `CONTEXT_OVERFLOW`, but an overflowing batch is deterministically unassemblable, so every later
   message inherited it and failed the same way. A probe confirmed it: after one overflow the pending
   list grew from one to two and a subsequent short message also returned `CONTEXT_OVERFLOW`, with no
   delivery ever occurring. Only `reset` or a process restart could clear it. Fixed by clearing the
   covered inputs on overflow, the same way a delivered response clears them, and the parameter that
   drives it is renamed from `delivery_succeeded` to `clear_pending` so the two remaining retention
   cases, generation failure and delivery failure, still read as deliberate. A new test pins it: an
   overflow followed by a short message now delivers that message and stores exactly one Turn holding
   only its text.

2. `TokenCompactor.should_compact` was defined twice, identically, so the second silently shadowed the
   first. Removed the duplicate.

3. The Orchestrator repeated the literal `4096` for its response reserve instead of importing
   `DEFAULT_MAX_RESPONSE_TOKENS`, which the assembler already shares with `CompactionPolicy`. That is
   the drift this project pinned a test for one feature ago; the constant is now imported.

4. The late-result test did not exercise the guarantee it names. It yielded once after both
   submissions resolved, which is not enough for a provider that ignored cancellation to reach its
   commit attempt, so deleting the generation-ownership check in `_claim_commit` left the whole suite
   green. The test now records the generation tasks themselves and joins them before asserting, so the
   ignoring provider runs all the way to the commit attempt and ownership is what stops it. Deleting
   the check now fails the suite.

Three other guarantees were confirmed by mutation: the combined-text rule for the newest explicit
input, clearing pending on delivery, and the overflow clearing added above.

## Instructions for Codex

Nothing further to fix. Confirm main CI succeeds after merge. When this Orchestrator is wired into
`pia-agent`, `CONTEXT_OVERFLOW` is the status that must reach the user as a message, since the harness
now drops that batch rather than retrying it.

## Resolution record: 2026-09-09, shared model token budget

Before main merge, the owner chose to remove the remaining duplicated token-budget configuration
rather than compare three values at runtime. One immutable `ModelTokenBudget(context_limit,
response_tokens=4096)` is now the sole input-budget configuration. The Assembler uses its
`input_tokens`, Compaction uses its context and response counts, and the Orchestrator passes the same
object to both. `CompactionPolicy.max_response_tokens`, Assembler's separate context/reserve arguments,
and Orchestrator's duplicate context/reserve fields are removed. This is a configuration refactor only;
trigger ratio, protected-tail ratio, overflow behavior, and user-visible policy do not change.
