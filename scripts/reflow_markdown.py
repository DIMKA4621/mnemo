#!/usr/bin/env python3
"""Reflow markdown paragraphs: join lines that were only wrapped for width.

Leaves fenced code blocks, tables, headings, blockquotes, horizontal rules
and YAML front matter untouched. Everything else (prose paragraphs and
list-item text) gets its wrapped lines rejoined into one line, since a
markdown renderer already collapses those internal breaks into one flowing
block — the wrapping only inflates line count in the source.

A paragraph is first joined into one string, then re-split one sentence per
line (not one line per paragraph) — guarded against splitting inside inline
code spans/links or right after a known abbreviation ("e.g.", "i.e.", "vs.",
version numbers like "v3.0.1."), so a genuine sentence boundary is what
starts a new line, never a column count. The guard is heuristic, not a full
parser — that is exactly why every run should be reviewed as a diff before
`--write` touches real files.

A file or directory can be named directly; git tracking status is never
consulted — a directory is walked recursively for every "*.md" under it,
tracked or not (skipping vendor/VCS directories, see VENDOR_DIR_NAMES).
Before writing anything, every reflow is verified against the original: the
two must be identical once whitespace is normalized away, i.e. the reflow
changed line breaks and nothing else. A file that fails this check is never
written — that would be a bug in this script, not something to force through.

Usage:
    python scripts/reflow_markdown.py                  # dry-run over the cwd
    python scripts/reflow_markdown.py docs/             # dry-run, one directory
    python scripts/reflow_markdown.py path/to/file.md   # dry-run, one file
    python scripts/reflow_markdown.py --check ...       # same, but exit 1 if
                                                         # anything needs reflowing
    python scripts/reflow_markdown.py --write ...       # apply changes in place
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^\s*#{1,6}\s")
BLOCKQUOTE_RE = re.compile(r"^\s*>")
HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|")
# Deliberately narrow: only real block-level HTML tags, never a bare "<" —
# CLI docs constantly use "<tag>"/"<path>"/"<agent>" as placeholder text, and
# a loose "starts with <" match was mistaking those for an HTML block start,
# which stopped paragraph collection mid-sentence and left inline code spans
# broken across two lines.
HTML_BLOCK_RE = re.compile(
    r"^\s*</?(div|table|details|summary|section|p|br|img|a|span|ul|ol|li"
    r"|blockquote|pre|code)\b",
    re.IGNORECASE,
)
LIST_MARKER_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s")
FRONT_MATTER_DELIM = "---"

_ABBREVIATIONS = {
    "e.g", "i.e", "etc", "vs", "no", "fig", "dr", "mr", "mrs", "ms", "st",
    "approx", "cf", "напр", "то", "рр", "ст", "ім", "див",
}
_ABBREV_END_RE = re.compile(
    r"(?:^|[\s(\[])(" + "|".join(re.escape(a) for a in _ABBREVIATIONS) + r")\.$",
    re.IGNORECASE,
)
# A short alnum label like "1.", "B1.", "T2.", "[NEW]." — a numbered/lettered
# item marker, not a sentence end, even though a capital letter follows it.
_LABEL_END_RE = re.compile(r"(?:^|[\s(\[])[A-ZА-ЯІЇЄҐ]{0,3}\d{1,3}\.$")
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]+(?=\s+[A-ZА-ЯІЇЄҐ0-9`*\[\"“(])")
_CODE_SPAN_RE = re.compile(r"`[^`]*`")
_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
_BOLD_SPAN_RE = re.compile(r"\*\*[^*]+\*\*")


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges that must never be split inside: inline code spans,
    markdown links and bold runs, where a bare '.' or ')' is not a sentence
    end (and where a still-open '**' must not be left dangling on its own
    line)."""
    spans = [m.span() for m in _CODE_SPAN_RE.finditer(text)]
    spans += [m.span() for m in _LINK_RE.finditer(text)]
    spans += [m.span() for m in _BOLD_SPAN_RE.finditer(text)]
    return spans


def _inside_span(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def split_sentences(text: str) -> list[str]:
    """Split one joined paragraph back into one sentence per line. Skips a
    candidate boundary that falls inside a protected span or right after a
    known abbreviation — when unsure, it leaves two sentences on one line
    rather than risk cutting one sentence in half."""
    spans = _protected_spans(text)
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        end = match.end()
        if _inside_span(match.start(), spans):
            continue
        candidate = text[start:end]
        if _ABBREV_END_RE.search(candidate) or _LABEL_END_RE.search(candidate):
            continue
        sentences.append(candidate.strip())
        start = end
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences if sentences else [text]


def _always_block_start(line: str) -> bool:
    """Lines that unambiguously stand on their own, no matter what came
    before — a blank line, a heading, a fence, and the like can never be a
    wrapped continuation of the previous line."""
    if not line.strip():
        return True
    return bool(
        FENCE_RE.match(line)
        or HEADING_RE.match(line)
        or BLOCKQUOTE_RE.match(line)
        or HR_RE.match(line)
        or TABLE_ROW_RE.match(line)
        or HTML_BLOCK_RE.match(line)
    )


def _inside_open_span(text_so_far: str) -> bool:
    """True while an odd number of '**' or backticks have appeared in the
    paragraph collected so far — i.e. a bold/code span is still open. A wrap
    that happens to land right before "3. something" mid-bold-span must not
    be mistaken for a real numbered list item starting there."""
    return text_so_far.count("**") % 2 == 1 or text_so_far.count("`") % 2 == 1


def reflow_text(text: str) -> tuple[str, bool]:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    changed = False

    if lines and lines[0].strip() == FRONT_MATTER_DELIM:
        out.append(lines[0])
        i = 1
        while i < n and lines[i].strip() != FRONT_MATTER_DELIM:
            out.append(lines[i])
            i += 1
        if i < n:
            out.append(lines[i])
            i += 1

    while i < n:
        line = lines[i]

        fence_match = FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            out.append(line)
            i += 1
            while i < n:
                out.append(lines[i])
                closed = lines[i].strip().startswith(marker * 3)
                i += 1
                if closed:
                    break
            continue

        if (
            not line.strip()
            or HEADING_RE.match(line)
            or BLOCKQUOTE_RE.match(line)
            or HR_RE.match(line)
            or TABLE_ROW_RE.match(line)
            or HTML_BLOCK_RE.match(line)
        ):
            out.append(line)
            i += 1
            continue

        # Paragraph or list-item text: collect this line plus every
        # following line that isn't itself a block boundary, and join them.
        para = [line]
        para_text_so_far = line
        j = i + 1
        while j < n:
            nxt = lines[j]
            if _always_block_start(nxt):
                break
            if LIST_MARKER_RE.match(nxt) and not _inside_open_span(para_text_so_far):
                break
            para.append(nxt)
            para_text_so_far += " " + nxt
            j += 1

        indent_match = re.match(r"^(\s*(?:[-*+]|\d+[.)])\s+|\s*)", para[0])
        indent = indent_match.group(1) if indent_match else ""
        joined_body = " ".join(
            [para[0][len(indent):].strip()] + [part.strip() for part in para[1:]]
        )
        sentences = split_sentences(joined_body)
        new_lines = [indent + sentences[0]] + [
            (" " * len(indent)) + s for s in sentences[1:]
        ]

        if new_lines != para:
            changed = True
        out.extend(new_lines)
        i = j

    return "\n".join(out) + "\n", changed


def read_preserving_newline(path: Path) -> tuple[str, str]:
    """Read a file's text plus the newline style it actually uses, so a
    reflow never turns into a spurious whole-file diff just because Python's
    default text-mode write would otherwise switch LF <-> CRLF.
    Path.read_text/write_text don't accept `newline=`, so this goes through
    open() directly."""
    with path.open("r", encoding="utf-8", newline="") as f:
        raw = f.read()
    newline = "\r\n" if "\r\n" in raw else "\n"
    return raw, newline


def write_preserving_newline(path: Path, text: str, newline: str) -> None:
    if newline != "\n":
        text = text.replace("\n", newline)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(text)


VENDOR_DIR_NAMES = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", "dist", "build", "site-packages",
}


def _norm_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_equivalent(old: str, new: str) -> bool:
    """True if `old` and `new` carry the same text once whitespace is
    collapsed — the only thing a correct reflow is allowed to change."""
    return _norm_whitespace(old) == _norm_whitespace(new)


def discover_markdown_files(paths: list[str]) -> list[Path]:
    """Resolve CLI arguments into concrete .md files. A directory (or the
    default, the cwd) is walked recursively; git tracking is never checked.
    A non-.md file named directly is skipped with a warning rather than
    reflowed — this tool only ever touches markdown."""
    roots = [Path(p) for p in paths] if paths else [Path(".")]
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            for candidate in sorted(root.rglob("*.md")):
                if not any(part in VENDOR_DIR_NAMES for part in candidate.parts):
                    files.append(candidate)
        elif root.suffix.lower() == ".md":
            files.append(root)
        else:
            print(f"skipping (not .md): {root}", file=sys.stderr)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*",
        help="files and/or directories to reflow (default: current directory, recursive)",
    )
    parser.add_argument("--write", action="store_true", help="write changes in place (default: dry-run)")
    parser.add_argument(
        "--check", action="store_true",
        help="report status per file and exit 1 if any needs reflowing; writes nothing",
    )
    args = parser.parse_args()

    files = discover_markdown_files(args.paths)

    any_needs_reflow = False
    any_mismatch = False
    for path in files:
        original, newline = read_preserving_newline(path)
        new_text, changed = reflow_text(original)

        if args.check:
            print(f"{'NEEDS REFLOW' if changed else 'OK'}: {path}")
            if changed:
                any_needs_reflow = True
            continue

        if not changed:
            continue
        any_needs_reflow = True

        if not content_equivalent(original, new_text):
            any_mismatch = True
            print(f"MISMATCH (not written, please report this): {path}")
            continue

        if args.write:
            write_preserving_newline(path, new_text, newline)
            print(f"reflowed: {path}")
        else:
            print(f"would reflow: {path}")

    if not any_needs_reflow:
        print("nothing to reflow" if not args.check else "all files already comply")

    if any_mismatch:
        return 2
    if args.check and any_needs_reflow:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
