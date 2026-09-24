# Jev routing core without live tools

## Scope

- Implement the README 1-6 runtime changes and the generic tool boundary needed by section 7.
- Keep Search provider, coding environment, Broker calls, and AWS deployment out of this branch.
- Use fake registered read tools to verify Jev selection, observation, re-selection, supersede, and persistence.

## Minimal sequence

1. Move Compaction to before model calls at 80% of the input budget; remove the post-answer trigger.
2. Make Memory review wait 12 hours, accept host Memory instructions, and use Jev to decide whether the writer LLM runs.
3. Add an injected Jev next-action adapter and registered tool contracts. A read-tool result joins current Context and is revisited by Jev. No live effectful tool is enabled.
4. Preserve no-tool conversation behavior and Reset; test with fakes and the full suite.

## Boundaries

- Search is a registered tool interface only. Provider choice remains open.
- Model-produced tool arguments never authorize an effectful action; that contract is deferred with actual execution tools.
- No real Jev, Search, AWS, or Broker call in automated tests.
