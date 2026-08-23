---
name: mn-jira-workflow
description: Manage tickets in this project's Jira board (MN, site mismartconfig.atlassian.net) through the atlassian MCP tools — capture a raw idea as a Draft, groom a Draft into a scoped To Do, move a ticket through In Progress / In Review / Done, and escalate to In Clarification when a decision needs a human. Use when the user dumps a raw thought/task idea to file in Jira, asks to groom/refine a Draft ticket, asks to move an MN ticket to a new status (start work, submit for review, mark done, send back for clarification), asks to log a plan/result/blocker as a comment on an MN ticket, or mentions an MN issue key (e.g. MN-123) in the context of its status or content.
---

# MN Jira Workflow

Manages tickets in project **MN** (Jira Cloud, cloudId `mismartconfig.atlassian.net`) via `mcp__atlassian__*` tools.
Full status table, transition IDs and the reasoning behind them are in `references/workflow.md` — read it before any transition; this file covers only what to internalize every time.

**Links**
- Board: https://mismartconfig.atlassian.net/jira/software/projects/MN/boards/2
- Any issue: `https://mismartconfig.atlassian.net/browse/<KEY>` (e.g. `.../browse/MN-4`)

**When an action has no MCP tool** (currently: attaching a file, deleting an issue — full list in `references/workflow.md`), don't skip it or just tell the user — fall back to the browser via `mcp__chrome-devtools__*`: navigate to the issue's `/browse/<KEY>` URL, take a snapshot, find the action in the Jira UI (the "•••" more-actions menu on the issue page covers both Attach and Delete), do it there, confirm with another snapshot.
If the chrome-devtools tools fail to connect, the browser profile isn't running — say so rather than giving up silently.

## Language: Ukrainian

Everything written into Jira — summary, description, comments — is in Ukrainian.
The only exception is identifiers that are themselves not language: file paths, function/class/variable names, CLI commands, tool names, issue keys, URLs.
Keep those exactly as they appear in the code or docs; translate everything around them.

## Core contract: description vs comments

- **Description** — the ticket's scoped spec, written once at grooming (Draft → To Do): goal, scope in/out, acceptance criteria, open questions.
  It stays that spec for the rest of the ticket's life.
  Small, targeted edits are fine — resolving an open question once confirmed, adding a line the spec was missing — but never replace it with a step-by-step execution plan or a progress narrative.
  If in doubt whether an edit is "small," it isn't — put it in a comment.
- **Comments** (`addCommentToJiraIssue`) — an append-only chronological log: the execution plan when work starts, what was done/tested for review, a reviewer's verdict, a clarification request.
  Never edit a past comment to fix history; add a new one instead.

## Before overwriting a description: check for inline-pasted images

A classic attachment (added via "Attach") is an independent object — it survives a full `description` rewrite untouched.
An image pasted directly into the description body is not: its only reference to existing is the image macro inside that description (Jira wiki markup `!filename|...!`, or — through this MCP's markdown rendering — a broken `![](blob:...)` link).
Overwriting the whole description drops that macro, and the underlying image can get silently deleted server-side, not just hidden from the rendered text.
Confirmed live on this board 2026-08-23: MN-5/6/7's original screenshots were real, working attachments, and vanished (404 from Jira's own attachment API, not just from the description) right after their descriptions got rewritten during grooming.

Before any `editJiraIssue` that replaces `description` (grooming a Draft, starting work, etc.), check the current description for one of these inline-image markers.
If found, tell the user before overwriting — they can re-attach it properly first (via "Attach", not paste) or accept losing it.
A real attachment already listed in the `attachment` field needs no such warning.

## The six actions

### 1. Capture a Draft
Trigger: user dumps a raw idea/thesis and wants it filed, unedited.
- `createJiraIssue` in project `MN`, issue type per content (default `Task`), the raw text as-is in the description.
  Don't groom it in the same step unless explicitly asked — Drafts are meant to hold un-cleaned input.
- No transition call needed: new issues land in **Drafts** automatically (project default).

### 2. Groom Draft → To Do
Trigger: asked to refine a specific Draft, or "process the drafts".
- `getJiraIssue` to read the raw draft.
- If ambiguous, do light research first (codebase, `mcp__mnemo-memory__search`) rather than guessing.
- `editJiraIssue`: rewrite `description` into goal, scope (in/out), acceptance criteria, open questions.
  This replaces the raw draft text — it's now the living spec.
- `transitionJiraIssue` → **To Do** (id `11`, see reference table).

### 3. Start work: To Do → In Progress
Trigger: work begins on the ticket.
- `addCommentToJiraIssue`: log the detailed execution plan as a dated snapshot.
  This is the only place the plan lives.
- Leave `description` as the groomed spec from step 2. Touch it only for a small, targeted addition to that same spec (e.g. an open question that just got answered) — never a rewrite into a step-by-step plan.
- `transitionJiraIssue` → **In Progress** (id `31`).

### 4. Submit for review: In Progress → In Review
Trigger: implementation is finished.
- `addCommentToJiraIssue`: what was done, what was tested/verified, outcome.
- `transitionJiraIssue` → **In Review** (id `41`).

### 5. Approve: In Review → Done
Trigger: review passed.
- `transitionJiraIssue` → **Done** (id `51`).
  Optional closing comment.

### 6. Escalate: any active status → In Clarification
Trigger: something needs a human decision — an ambiguity or disagreement that can't be resolved without external input.
- `addCommentToJiraIssue`: state exactly what's unclear and what decision is needed.
- Note the ticket's *current* status before transitioning — needed to resolve step 7.
- `transitionJiraIssue` → **In Clarification** (id `21`).

### 7. Resolve Clarification → return
Trigger: a human answered in a comment.
- `getJiraIssue`/comments to read the human's answer.
- **Judgment call, not a fixed rule**: if the answer resolves/confirms things as they were, transition back to the status the ticket was in before Clarification.
  If the answer implies rework, transition to **In Progress** (or whichever earlier status the rework actually requires).
  There is no single "Clarification always returns to X."

See `references/workflow.md` for the full status/transition ID table and why these IDs are safe to hardcode for this board.
