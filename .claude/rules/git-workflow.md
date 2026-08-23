# Git workflow — branches, pull requests, releases

Binding for the team lead. Defines what three specific instructions mean in this
repo. This is on top of the global git rule (ask before every commit/push,
Conventional Commits, never add attribution) — it doesn't replace it.

## Starting new work — always a fresh branch off the latest master

Never commit new work directly on `master`, and never branch off whatever is
currently checked out without checking it first.

1. `git fetch origin`.
2. Compare local `master` to `origin/master`. If they differ, sync local
   `master` to match `origin/master` — but don't force-reset it without asking
   if a simple fast-forward isn't possible; a stale or diverged local `master`
   is worth flagging rather than silently overwriting.
3. Create the new feature branch from that up-to-date `master`. If the
   working tree has other uncommitted changes in it (e.g. a concurrent
   session's live edits), don't touch them — use an isolated `git worktree`
   for the new branch instead of checking it out in the main tree.

## Continuing existing work — same branch is fine

The "always fresh branch" rule above is about *starting* new work, not
every time work on a task resumes. If the user says to continue/pick up
a task that already has a branch (in progress, or with an open PR), stay
on that branch — don't fetch master and cut a new one just because it's
a new session.

- Ask which branch/PR to resume if it isn't obvious from context.
- Before committing more work on it, check the branch isn't stale vs.
  its own history in a way that matters (e.g. master moved far ahead and
  a rebase/merge is warranted) — but that's a judgment call to flag, not
  a reason to default to a fresh branch.
- A fresh branch is still the default for genuinely new, unrelated work.

## "Закоміть і підготуй пул-реквест" / "prepare a pull request"

This means: the work lives on its own branch, not on `master`.

1. Commit on that feature branch — propose the message, get confirmation,
   same as every commit (global rule).
2. Push the branch.
3. Open the PR with `gh pr create` (title + summary + test plan, the usual
   shape). **Never merge it.** The user reviews and accepts pull requests
   themselves — this project's git-safety-protocol default (no unrequested
   destructive or shared-state actions) extends to PR merges specifically:
   opening one is additive and reversible, merging one is not mine to decide.

## "Створи реліз" / "створи драфт релізу" / "create a release"

- **Always a draft, never a direct/published release.** `gh release create
  <tag> --draft ...` — the `--draft` flag is not optional, regardless of how
  the request is phrased. There is no instruction that produces a
  non-draft release; if the user wants to publish it, that's a separate,
  explicit step on their side.
- **Always cut from `master`, never from a feature branch.** A release
  reflects what the relevant PR looks like *after* merge, not the branch's
  state before it.
- **If the relevant branch/PR isn't merged into `master` yet when asked for a
  release, stop and say so** — ask the user to accept the PR first, then
  create the draft release once `master` actually contains it. Don't create
  the release from the unmerged branch as a stand-in.
