# MN board — statuses and transitions

Site: `mismartconfig.atlassian.net` (pass as `cloudId` to every `mcp__atlassian__*` call).
Project key: `MN`.
Software project, team-managed (`simplified: true`).

## Status table

| Status | statusId | Category | Category color |
|---|---|---|---|
| Drafts | 10009 | To Do | blue-gray |
| To Do | 10004 | To Do | blue-gray |
| In Clarification | 10005 | In Progress | yellow |
| In Progress | 10006 | In Progress | yellow |
| In Review | 10007 | In Progress | yellow |
| Done | 10008 | Done | green |

New issues are created in **Drafts** — that's the project's configured initial status, not a special case to handle.

## Transition IDs

Discovered by creating a probe issue (`MN-4`) and reading `getTransitionsForJiraIssue`.
Every transition below came back with `"isGlobal": true` — meaning this board's workflow allows any of these transitions from any current status, not a fixed linear chain.
That's why the IDs are safe to hardcode here instead of re-discovering them on every call.

| Transition id | Target status |
|---|---|
| 2 | Drafts |
| 11 | To Do |
| 21 | In Clarification |
| 31 | In Progress |
| 41 | In Review |
| 51 | Done |

Use with `transitionJiraIssue(cloudId, issueIdOrKey, {"id": "<transition id>"})`.

If a transition call ever fails (e.g. board workflow gets edited later to add conditions), fall back to calling `getTransitionsForJiraIssue` for that specific issue and use the id it returns — don't assume this table is permanent, just that it's the fast path today.

## Issue types available in MN

Epic, Story, Feature, Task, Bug, Request, Subtask.
Default to `Task` for a Draft when the user doesn't specify one.

## Gaps in the atlassian MCP toolset — browser fallback

The `mcp__atlassian__*` tools have no attachment/file-upload endpoint and no issue-delete endpoint.
For these two (or anything else that turns out missing), don't stop — do it through the browser via `mcp__chrome-devtools__*`:

1. `navigate_page` (or `new_page`) to `https://mismartconfig.atlassian.net/browse/<KEY>`.
2. `take_snapshot` to see the page's current UI.
3. On the issue view, both **Attach** and **Delete** live under the "•••" more-actions menu near the top of the issue — click it, then the matching option from the snapshot.
4. For attach: `upload_file` on the file input/drop zone that appears.
   For delete: confirm the dialog that appears (`click` its confirm button, or `handle_dialog` if it's a native browser dialog rather than a Jira modal).
5. `take_snapshot` again to confirm the action landed (attachment listed, or — for delete — navigation away from the now-gone issue).

This depends on the chrome-devtools MCP server actually being connected to a running browser profile.
If `list_pages` or any other chrome-devtools call fails to connect, the browser isn't up — say so plainly rather than retrying blindly or claiming the action succeeded.

Plans and results still default to `description`/comments per the core contract even where an attachment *is* possible through this fallback — only reach for an actual file attachment when the user explicitly asks for one.
