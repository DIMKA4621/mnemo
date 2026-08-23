# Markdown formatting — no width-based line wrapping

Binding for the team lead and every subagent, same reach as `v3-build.md`.
Applies to every `.md` file in this repo: `CLAUDE.md`, `README.md`, `docs/*.md`, `.claude/rules/*.md`, `.claude/agents/*.md`, `.claude/memory/**/*.md`, everything.

## The rule

**Never break a line only because it got long.** A markdown renderer (GitHub, Claude Code, any viewer) collapses a paragraph's internal line breaks into one flowing block regardless of how the source is wrapped — hard-wrapping at a fixed width (e.g. 80 columns) changes nothing about how the file renders, it only multiplies the line count in the source for no benefit, and that inflated line count is what gets read back into context on every search hit or file read.

- One logical unit — one sentence, one list item, one table row — stays on one line, however long that line is.
- A genuinely long, multi-clause sentence may be split across lines if that reads better, but the split goes at a real boundary (sentence end, clause break) chosen for readability — never at a column count.
- New line only for a real structural reason: a new sentence, a new paragraph (blank line), a new list item, a new heading.
  Not "this line hit ~80 characters."
- Code blocks, tables, and blockquotes keep whatever line structure they already need — this rule is about prose paragraphs and list-item text, not about reformatting things that have their own syntax.

## Why this exists

Multiple past sessions and subagents — which don't share context with each other — wrote `.claude/memory/logs/*.md`, `CLAUDE.md` and other docs with a rigid ~78–80 column wrap, turning one real sentence into 4–6 short lines.
Checked directly: this repo's own files already show the pattern.
It roughly triples the line count of every affected file for zero rendering difference, and it is exactly the kind of thing that quietly bloats what gets pulled back into context on every memory search or file read.

## Cleanup tooling

`scripts/reflow_markdown.py` mechanically joins wrapped prose/list-item lines back together, then re-splits the result one sentence per line (fenced code, tables, headings, blockquotes and horizontal rules are left untouched).
The sentence-boundary detection is heuristic — it guards against splitting inside inline code/links/bold spans and right after a known abbreviation or numbered label, but it is not a full parser.

Pass a file or a directory; a directory is walked recursively for every `.md` under it, regardless of whether git tracks it (vendor/VCS directories like `.git`, `node_modules`, `.venv` are skipped automatically).
Before writing anything, a reflow is checked against the original — the two must be identical once whitespace is collapsed — and a file that fails that check is never written, only reported as a mismatch.
An already-compliant file is left untouched and never reported as changed.

- `--check <path>` — report OK / NEEDS REFLOW per file, exit 1 if anything needs it, writes nothing.
- `--write <path>` — apply the reflow in place.
- No flag — the same listing as `--check`, but always exits 0 (a plain preview).

## Before committing

Run `python scripts/reflow_markdown.py --check <path>` over every new or changed `.md` file (or the whole repo) before committing.
If it reports anything, review with `--write` on just those files and re-read the diff — this is a heuristic tool, not a substitute for eyes on the result.
