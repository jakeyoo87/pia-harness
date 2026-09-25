# Jev routing core without live tools

## Scope

- Implement the README 1-6 runtime changes and the generic tool boundary needed by section 7.
- Keep Search provider, coding environment, Broker calls, and AWS deployment out of this branch.
- Use fake registered read tools to verify Jev selection, observation, re-selection, supersede, and persistence.

## Minimal sequence

1. Move Compaction to before model calls at 80% of the input budget; remove the post-answer trigger.
2. Make Memory review wait 12 hours, accept host Memory instructions, and use Jev to decide whether the writer LLM runs.
3. Add an injected Jev next-action adapter and registered tool contracts. A read-tool result joins current Context and is revisited by Jev. No live effectful tool is enabled.
4. Route answer-only conversations through Jev as well. On `answer`, Jev returns
   `NONE / UPDATE / FORGET` in the same response; the text-generation model no
   longer chooses MemoryAction. Reset bypasses Jev and atomically erases the
   long-term Memory while starting a new session. Old Turns are excluded from
   the new Context and expire under the existing retention policy.
   Test both paths with fakes and the full suite.

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
  longer has a model action or two-turn marker; the host's Reset request owns it.
- Before PIA upgrades its pia-harness pin, its `ConversationStore` adapter must
  implement the stronger atomic `reset_active_session` erasure contract and its
  Orchestrator construction must supply Jev `choose_next`. This branch does not
  deploy or change PIA.
