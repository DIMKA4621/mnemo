# Binding rules — mnemo v3 build

These rules bind the team lead **and every teammate**. Subagents do not inherit
`CLAUDE.md`, but they do load `.claude/rules/` — so the shared discipline lives
here, once, instead of being copied into every agent file.

## Source of truth

Read before acting; do not re-investigate or re-litigate what is already settled:

- **`docs/Memory-design-v3.md`** — *what and why*. Architecture and settled
  decisions (section 13 is the decision list). Deliberately contains no
  implementation plan.
- **`docs/Memory-requirements-v3.md`** — *what must hold*. FR/NFR are the
  acceptance criteria.
- **`docs/Memory-implementation-v3.md`** — *how and in what order*. Stack,
  blocks A–L, phases 0–7, each with a `✅ Перевірка` checklist.
- **`docs/Memory-contracts-v3.md`** — *the exact shapes*. Module ownership (§1 —
  one file, one owner), the store schema, registry, HTTP/WS API, MCP tools, the
  CLI surface and every env var; §14 records the lead's decisions on it, §15 the
  windows-native merge legacy, §16 the phase 2→3→4 seam.

The four docs must agree with each other and with the code. If they do not, or
if something material is missing, **stop and report to the team lead** — do not
decide it yourself.

## Git

- **Never commit or push.** The user approves every commit; the lead performs it.
- **Never** add `Co-Authored-By`, `Co-Founded-By` or any attribution line —
  anywhere, no exceptions.
- Conventional Commits when a message is proposed.

## Working discipline

- **v3 replaces v2 in place.** Never create a parallel v2/v3 code path.
- **A phase is not done until its `✅ Перевірка` passes** — with real output as
  evidence, not "it looks right".
- Stop at architectural forks and hand the decision to the team lead. Surface
  unexpected complexity instead of pushing through it.
- Keep changes inside the agreed scope; mention unrelated problems, do not
  silently fix them.
- Report outcomes faithfully: if something fails or was skipped, say so plainly
  and show the output. Never claim a check passed that you did not run.

## Architecture invariants (survive from v2 — do not break)

- **One-way sync:** `.md → index`. The index is never edited directly and is
  never a source of truth; it stays disposable and rebuildable from the `.md`.
- **Prune is mandatory** — deleted/renamed files must disappear from the index.
- Deterministic chunk ids; idempotent reconcile (a no-change run does nothing).
- Vector search primary, FTS5/BM25 secondary, blended with RRF.
- **The model is downloaded only by an explicit `warmup`** — never implicitly by
  a hook, a service or a search.
- Everything stays on **loopback** by default, guarded by the token; nothing is
  exposed outward without an explicit decision.
- Nothing may block a Claude Code session: faces and hooks return immediately.
- **No console windows** on Windows for any spawned process: `pythonw` (a
  GUI-subsystem binary cannot own a console) plus `CREATE_NO_WINDOW`.
  `DETACHED_PROCESS` alone is not enough — a child calling `AllocConsole()`
  gets a real window — and it must **never be OR-ed with** `CREATE_NO_WINDOW`,
  which Windows then ignores, silently turning the guard into a no-op. Every
  spawn goes through `service_ctl.spawn_detached`, not hand-assembled flags.

## Conventions

- Code, comments, docstrings, commit messages and English-language docs: **English**.
  The v3 docs (`docs/Memory-{design,requirements,implementation,contracts}-v3.md`
  and `docs/Setup-design.md`) stay **Ukrainian**; `CLAUDE.md`, `README.md` and the
  `mnemo-adopt` skill stay **English**.
- Python: PEP 8, type hints. Match the surrounding code's style and comment density.
- **The local machine is Windows.** Any local shell command must be written in
  **PowerShell** syntax — never bash. (Remote Linux hosts use their own shell.)
- Native only: no Docker, no WSL2, no PowerShell 7 requirement (Windows
  PowerShell 5.1 is the baseline), no system `PATH` mutation.
