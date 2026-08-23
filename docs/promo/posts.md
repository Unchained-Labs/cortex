# Posts for 0.4.0

Written to `brand/voice.md`: numbers over adjectives, name the cost, no
superlative that cannot be checked in the same sentence. Attach
`docs/assets/cortex-demo.gif` (the loop) or the full film from the site.

---

## 1. The launch post

> cortex 0.4.0 is out.
>
> A self-hosted brain for a household or a team: shared and private
> markdown vaults, hybrid search, peer chat, and an agent that reads your
> notes before it answers — on a model you run yourself.
>
> pip install cortxai
> unchained-labs.github.io/cortex

*Attach: cortex-demo.gif*

---

## 2. The design-decision post (my pick)

> Shipped a rules engine that files notes for you. Three things it will
> never do:
>
> There is no delete action.
> Preview always comes before apply, so you see which note moves where.
> Every change is logged, because "where did my note go" needs an answer.
>
> It moves your writing. That is not a place to be clever.
>
> unchained-labs.github.io/cortex

---

## 3. The identity post

> The agent in cortex can propose changes to its own instructions. It
> cannot make them.
>
> A system that quietly rewrites what it was told is one nobody can reason
> about, and the failure is silent: you would never know which version
> answered you.
>
> So proposals queue with a reason, and a human accepts or discards.

---

## 4. Reply to the Second Brain Architecture diagram

> Built three of these into cortex this week: typed memory (people,
> projects, preferences), note templates, and identity as a file the agent
> can propose edits to.
>
> Skipped the directory taxonomy. Whittaker et al., 85,000 refinding
> actions: folder retrieval 58.8s, search 17.2s, tags 1% of accesses.
> Filing is the part that does not pay.
>
> Capture stays unfiled. Search is the way back.

*Only post this as a reply if the original author would read it as
engagement rather than correction. It disagrees with half their diagram.*

---

## 5. The anti-engagement post

> The daily view in cortex shows a handful of tasks and then says "that is
> everything for today".
>
> It never shows a growing count of what you have not done. That is a debt
> counter, and every product that shipped one documents the same ending:
> people stop opening it.
>
> No streaks either. Your absence is not a problem to solve.

---

## 6. Short thread

> 1/ cortex 0.4.0. A self-hosted brain for a household: markdown vaults,
> hybrid search, peer chat, and an agent on a model you run yourself.
>
> 2/ New: rules that file notes on a schedule. No delete action, preview
> before apply, every change logged. It moves your writing, so it is
> deliberately boring.
>
> 3/ New: typed memory. It knows Priya is a person and the boiler is a
> project, so "who do we call about the boiler" is a lookup rather than a
> search through prose.
>
> 4/ New: identity.md, read into every conversation. The agent can propose
> changes to it and cannot make them.
>
> 5/ 207 tests, MIT, alpha. Bring Ollama, vLLM, OpenRouter, LiteLLM or an
> Anthropic key — cortex hosts no model.
>
> pip install cortxai · unchained-labs.github.io/cortex

---

## Notes

- Say **alpha** wherever there is room. It is true and it sets expectations.
- "cortex hosts no model" is worth repeating; it is the first question
  people ask.
- Do not claim it is private *and* leave out that a public model endpoint
  sends your notes off the box. The product warns about this; a post that
  does not is overselling it.
