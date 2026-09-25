# Jev routing core without live tools

## Scope

- Implement the README 1-5 runtime changes and the generic tool boundary needed by section 6.
- Keep Search provider, coding environment, Broker calls, and AWS deployment out of this branch.
- Use fake registered read tools to verify Jev selection, observation, re-selection, supersede, and persistence.

## Minimal sequence

1. Move Compaction to before model calls at 80% of the input budget; remove the post-answer trigger.
2. Make Memory review wait 12 hours, accept host Memory instructions, and use Jev to decide whether the writer LLM runs.
3. Add an injected Jev next-action adapter and registered tool contracts. A read-tool result joins current Context and is revisited by Jev. No live effectful tool is enabled.
4. Route answer-only conversations through Jev as well. On `answer`, Jev returns
   `NONE / UPDATE / FORGET` in the same response; the text-generation model no
   longer chooses MemoryAction. Remove the unused conversation Reset API,
   store method, coordination state, and tests. Test the remaining paths with
   fakes and the full suite.

## Boundaries

- Search is a registered tool interface only. Provider choice remains open.
- Model-produced tool arguments never authorize an effectful action; that contract is deferred with actual execution tools.
- No real Jev, Search, AWS, or Broker call in automated tests.

## Review follow-up

- Require the host Memory instruction; a Jev-selected `UPDATE` or `FORGET`
  runs the writer without a second unchanged gate, while automatic reviews may
  skip it after Jev's unchanged decision.
- Give the read-only Jev loop one configurable 120-second overall time budget. Do not add a fixed tool-call count or separate duplicate-call heuristic. An expired loop clears its pending input without invoking a tool again.
- The concrete `next_action` value is `answer` or a registered tool ID (for
  example `search`), not one of the README's four explanatory categories.
  Non-answer choices defer MemoryAction as `NONE`. Whole-Memory deletion no
  longer has a model action or two-turn marker. Targeted `FORGET` can leave an
  empty Memory document when the last retained fact is removed.
- Before PIA upgrades its pia-harness pin, remove its Telegram `/reset` command,
  `ConversationService.reset`, DynamoDB `reset_active_session` and
  `SessionConflictError` import, related work-kind/ordering/tests/docs, then
  supply Jev `choose_next` at Orchestrator construction. This branch does not
  deploy or change PIA.
