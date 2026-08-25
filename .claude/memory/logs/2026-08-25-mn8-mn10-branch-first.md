# 2026-08-25 — MN-8/MN-10: branch-first correction, and the git-workflow.md fix it produced

## What happened

Both MN-8 ("тека" → "директорія") and MN-10 (console i18n) were started by editing files directly on `master` — the branch was never created up front, only planned to be created "before the commit." By the time MN-10's `ui-dev` step 1 landed (a large refactor of `app.js`/`shell.js`/`index.html`), the working tree on `master` carried uncommitted, unbranched changes from *two* tasks tangled together in the same files (`page-memory.js` in particular: 2 hunks from MN-8's wording fix, 1 hunk from MN-10 step 1's `STATUS_LABEL` → `statusLabel()` conversion).

User caught this as a repeat pattern ("вже який раз помічаю") — this is not the first time an agent in this project started editing before branching.

## Fix applied to this instance

Since nothing was committed yet, recovery was possible without loss:

1. Asked the user how to structure it going forward (`AskUserQuestion`): two separate branches per ticket, or one shared branch since the work was already tangled and the user had handed over both tasks together ("Приступай до задач MN-8 та MN-10 ... першу робиш восьму, потім займаєшся десятою").
   **User chose one shared branch.**
2. Created `feature/mn-8-mn-10-console-ui-text` off `master` (this captured all the uncommitted working-tree changes, since branch creation doesn't touch uncommitted diffs).
3. `page-memory.js` had cleanly separable hunks (confirmed via `git diff` before committing anything) — wrote a small patch file with just the 2 MN-8 hunks, `git apply --cached` to stage only those, committed MN-8 alone.
4. Everything else (`app.js`'s 577-line diff, `index.html`, `shell.js`, `page-journal.js`, plus `page-memory.js`'s 3rd hunk) went into MN-10 step 1's own commit — **MN-8's wording fix in `app.js` was not separately preserved as its own commit**, because `ui-dev`'s i18n extraction read the post-MN-8 file state and absorbed the already-corrected "директорія" text directly into the new `i18n/uk.js` dictionary.
   This is fine and expected, not a loss — the wording survives, just inside the refactor rather than as an isolable diff.
   Flagging so a future session doesn't go looking for a "clean MN-8-only app.js diff" that no longer exists once a later step has rewritten the same lines.

## Rule change

`.claude/rules/git-workflow.md`'s "Starting new work" section rewritten: the branch is explicitly **step zero**, created before the first file edit, not before the commit.
Also added explicit guidance for the "user hands over several tasks at once" case (exactly this session's MN-8-then-MN-10 request): one shared, moderately-generic branch name is fine, no need for a branch per ticket when the user is already treating them as one batch — ask if it's ambiguous whether tasks belong together.

## Lesson for next time

**Before opening any editor tool on a task's files — even one that looks like a one-line fix — create the branch first.** The temptation ("I'll branch before I commit") is exactly how this happens: a small task grows, or a second task gets layered on top, and by the time a commit is due, disentangling unbranched work from `master` costs real effort (patch surgery, as done here) that three commands up front would have avoided entirely.
