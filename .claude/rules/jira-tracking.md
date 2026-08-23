# Jira tracking across roles — binding rule

Binds the team lead **and every subagent**, same reach as `.claude/rules/v3-build.md`.
This file says **who does what and when** with the MN Jira board; the `mn-jira-workflow` skill says **how** (statuses, transitions, description/comment convention, browser fallback).
Read the skill before touching a ticket for the first time in a session.

## Single writer

**Only the team lead calls `mcp__atlassian__*`.** No subagent creates, edits, comments on, or transitions a ticket directly — same reasoning as "never commit or push": one thing writing to shared, externally-visible state, everyone else reports back to it.
A subagent's job is to produce the *content* (a plan, a test report, a review verdict); the lead's job is to put that content into the right place on the right ticket and move it.

When the lead delegates work that's tied to a ticket, it passes the ticket key (e.g. `MN-12`) in the prompt so the subagent's report can reference it — but not every delegated task has to trace to a ticket; use judgment (see below).

## Implementation is delegated, not solo

The lead's own hands stay on Jira reads/writes, scoping, and orchestration — not on writing the code.
When a ticket reaches **In Progress**, the actual implementation goes to the matching specialist (`engine-dev`, `service-dev`, `ui-dev`, `platform-dev`, `docs-keeper`), verification goes to `tester`, review goes to `reviewer` — via the `Agent` tool, the same as any other delegated work.
The lead implementing a ticket itself, end to end, defeats the point of having those roles: no second pair of eyes on the code, no independent test pass, everything shaped by one context's blind spots.
Judgment call, exception: a change small enough that spinning up an agent costs more than it saves (a one-line fix, a single obvious constant) — but default to delegating, not to solo.

## Who does what

- **Team lead (you)** — owns intake and every write:
  - When the user describes substantive new work (a feature, fix, or phase — not a one-off question or trivial edit), file it in Jira: a raw **Draft** if it's just been described, or straight to **To Do** if it's already well-scoped.
  - Resolves ambiguity either directly with the user, or by delegating the grooming/research to `planner` and writing the result back into the ticket's description.
  - Owns every `In Clarification` escalation and return — it's the one talking to the user, so it's the one who knows when an answer resolves things.
  - Performs every `editJiraIssue`, `addCommentToJiraIssue`, and `transitionJiraIssue` call, using content the relevant subagent produced.

- **planner** — produces the grooming (Draft → To Do) content and the detailed execution plan (To Do → In Progress).
  Returns plan text to the lead; does not call Jira tools itself.

- **engine-dev / service-dev / ui-dev / platform-dev / docs-keeper** — when finishing implementation work tied to a ticket, report completion and a summary to the lead.
  The lead moves the ticket to **In Review**.

- **tester** — produces the verification report (pass/fail with evidence) as it already does.
  The lead posts it as the In Review comment.

- **reviewer** — produces the verdict: approve (→ **Done**) or send back with reasons (→ **In Clarification** or **In Progress**, per its own read of what the diff needs).
  The lead performs the transition the verdict implies.

## Scope judgment

Not everything is a ticket.
A full feature, a bug fix, a phase of work — yes.
A one-off question, a trivial edit, "run the tests" — no. If unsure whether something rises to ticket-worthy, ask the user rather than guessing either way.
