# Design Decisions

Why this is built the way it is. These are the tradeoffs I'd defend in review.

## Orchestrator per phase, not one agent loop

The production version I built ran on a single coding-agent loop. It shipped fast and got used, but every new capability was bounded by what that loop could do natively — the phases weren't independently controllable, so swapping one step meant reworking the whole thing.

So I rebuilt it as a LangGraph graph where each phase is its own node. Adding a Jira adapter, swapping the log parser, or re-ordering steps touches one node, not the whole pipeline. Each node is independently testable and debuggable. The upfront cost is a bit more structure; the payoff is extensibility.

## The git phase is deterministic — no LLM

Git history is the step most likely to name the actual culprit, so it stays fully inspectable: plain `git log` / `git show`, no model in the loop. The LLM *reasons over* the evidence; it doesn't *produce* the evidence. This keeps the highest-signal step verifiable and cheap.

## Attach the suspect's diff, not just its message

The first version's report said *"recently modified to simplify discount lookup"* — accurate but vague. Feeding the prime suspect's actual **diff** into the report prompt sharpened it to *"replaced `DISCOUNTS.get(code, 0.0)` with `DISCOUNTS[code]`, removing the default."* Confidence rose from 0.80 to 0.90 on the demo.

The diff is bounded — top 2 suspects, 1500 chars each — so a noisy incident can't blow up the prompt.

## Cheap model to parse, strong model to synthesize

Log parsing is close to extraction, so it runs on `gpt-4o-mini`. Root-cause synthesis is the judgment call, so it runs on `gpt-4o`. Two dials, matched to the difficulty of each phase.

## Structured output at the boundary

Every LLM call returns a validated pydantic object, with retries and backoff. Nodes never touch raw model text, so a malformed response fails in exactly one place instead of leaking a `None` three phases downstream.

## Calibrated confidence + honest degradation

The report carries a 0–1 confidence, and the pipeline accumulates warnings rather than crashing when a source is missing. A triage tool that silently drops a phase — or projects false certainty — is worse than no tool at all.
