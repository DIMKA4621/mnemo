---
name: mn-jira-workflow
description: Manage tickets in this project's Jira board (MN, site mismartconfig.atlassian.net) through the atlassian MCP tools — capture a raw idea as a Draft, groom a Draft into a scoped To Do, move a ticket through In Progress / In Review / Done, and escalate to In Clarification when a decision needs a human. Use when the user dumps a raw thought/task idea to file in Jira, asks to groom/refine a Draft ticket, asks to move an MN ticket to a new status (start work, submit for review, mark done, send back for clarification), asks to log a plan/result/blocker as a comment on an MN ticket, or mentions an MN issue key (e.g. MN-123) in the context of its status or content.
---

# MN Jira Workflow

Manages tickets in project **MN** (Jira Cloud, cloudId `mismartconfig.atlassian.net`) via
`mcp__atlassian__*` tools. Full status table, transition IDs and the reasoning behind them
are in `references/workflow.md` — read it before any transition; this file covers only what
to internalize every time.

**Links**
- Board: https://mismartconfig.atlassian.net/jira/software/projects/MN/boards/2
- Any issue: `https://mismartconfig.atlassian.net/browse/<KEY>` (e.g. `.../browse/MN-4`)

**When an action has no MCP tool** (currently: attaching a file, deleting an issue — full list
in `references/workflow.md`), don't skip it or just tell the user — fall back to the browser
via `mcp__chrome-devtools__*`: navigate to the issue's `/browse/<KEY>` URL, take a snapshot,
find the action in the Jira UI (the "•••" more-actions menu on the issue page covers both
Attach and Delete), do it there, confirm with another snapshot. If the chrome-devtools tools
fail to connect, the browser profile isn't running — say so rather than giving up silently.

## Core contract: description vs comments

- **Description** — the ticket's current living state. Overwrite it (`editJiraIssue`) at every
  meaningful transition. Always reflects "what's true about this ticket right now."
- **Comments** (`addCommentToJiraIssue`) — an append-only chronological log. Never edit a past
  comment to fix history; add a new one instead.

## The six actions

### 1. Capture a Draft
Trigger: user dumps a raw idea/thesis and wants it filed, unedited.
- `createJiraIssue` in project `MN`, issue type per content (default `Task`), the raw text
  as-is in the description. Don't groom it in the same step unless explicitly asked — Drafts
  are meant to hold un-cleaned input.
- No transition call needed: new issues land in **Drafts** automatically (project default).

### 2. Groom Draft → To Do
Trigger: asked to refine a specific Draft, or "process the drafts".
- `getJiraIssue` to read the raw draft.
- If ambiguous, do light research first (codebase, `mcp__mnemo-memory__search`) rather than
  guessing.
- `editJiraIssue`: rewrite `description` into goal, scope (in/out), acceptance criteria, open
  questions. This replaces the raw draft text — it's now the living spec.
- `transitionJiraIssue` → **To Do** (id `11`, see reference table).

### 3. Start work: To Do → In Progress
Trigger: work begins on the ticket.
- `editJiraIssue`: rewrite `description` with the detailed execution plan.
- `addCommentToJiraIssue`: log the same plan as a dated snapshot.
- `transitionJiraIssue` → **In Progress** (id `31`).

### 4. Submit for review: In Progress → In Review
Trigger: implementation is finished.
- `addCommentToJiraIssue`: what was done, what was tested/verified, outcome.
- `transitionJiraIssue` → **In Review** (id `41`).

### 5. Approve: In Review → Done
Trigger: review passed.
- `transitionJiraIssue` → **Done** (id `51`). Optional closing comment.

### 6. Escalate: any active status → In Clarification
Trigger: something needs a human decision — an ambiguity or disagreement that can't be
resolved without external input.
- `addCommentToJiraIssue`: state exactly what's unclear and what decision is needed.
- Note the ticket's *current* status before transitioning — needed to resolve step 7.
- `transitionJiraIssue` → **In Clarification** (id `21`).

### 7. Resolve Clarification → return
Trigger: a human answered in a comment.
- `getJiraIssue`/comments to read the human's answer.
- **Judgment call, not a fixed rule**: if the answer resolves/confirms things as they were,
  transition back to the status the ticket was in before Clarification. If the answer implies
  rework, transition to **In Progress** (or whichever earlier status the rework actually
  requires). There is no single "Clarification always returns to X."

See `references/workflow.md` for the full status/transition ID table and why these IDs are
safe to hardcode for this board.
