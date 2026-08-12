"""Heading-aware markdown chunking via semantic-text-splitter.

We do not reinvent splitting; we wrap the Rust-backed MarkdownSplitter.
A file smaller than the minimum capacity naturally yields a single chunk.

Each chunk carries its character span in the source file. Those offsets are
stored in the index so the UI can draw real chunk boundaries over the raw
``.md`` instead of re-splitting it — a second splitting path is exactly the
kind of drift we avoid.

Three rules run in order, and each exists for a different reason:

1. **Split by characters** (``CHUNK_CAPACITY``). Characters are a proxy for
   the thing that matters, but on our corpora they are a *better-retrieving*
   proxy than tokens: measured on the real bank, a token-capacity rule with
   the same median chunk size scored 0.50 MRR against 0.53, and raising its
   ceiling made it worse still. So the metric stays as it was.
2. **Fold runts forward** (``CHUNK_MERGE_FLOOR_CHARS``) — a heading with no
   body of its own belongs to the section it opens.
3. **Cap by tokens** (``CHUNK_TOKEN_CEILING``), and only then. This is the
   guarantee characters cannot give: the ratio ranges from 1.40 to 4.04
   characters per token across our banks, so a 1200-character chunk can be
   822 tokens against a 512-token window, and its tail would vanish from the
   index with no error and no warning. The cap re-splits only a chunk that
   actually exceeds the window, so on text that never does — like everything
   here today, which peaks at 469 — the boundaries are exactly what step 1
   produced, and the measured retrieval is unchanged.

The tokenizer ships with the model, so a machine that never ran ``warmup``
cannot count tokens and step 3 is skipped. That machine also cannot embed,
so nothing is being truncated yet; the index it builds is rebuilt when a real
provider arrives. ``chunker_key()`` names whichever rules are in force, and
the index records it, so a change rebuilds rather than leaving two
incompatible chunkings in one database.
"""
from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from semantic_text_splitter import MarkdownSplitter

from .config import (
    CHUNK_CAPACITY,
    CHUNK_MERGE_FLOOR_CHARS,
    CHUNK_TOKEN_CEILING,
    EMBEDDING_MODEL,
    MODEL_CACHE,
)


@dataclass(frozen=True)
class Chunk:
    """One indexable unit: a section of a markdown file."""

    index: int
    text: str
    heading: str
    start: int   # character offset into the source text
    end: int     # exclusive


def _first_heading(text: str) -> str:
    """Best-effort: first markdown heading inside the chunk, else ''."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


@functools.lru_cache(maxsize=1)
def _tokenizer_file() -> Path | None:
    """The active model's tokenizer inside the shared cache, or None.

    Found by walking the cache rather than by building a path: fastembed maps
    ``intfloat/multilingual-e5-large`` onto its own ONNX mirror
    (``qdrant/multilingual-e5-large-onnx``), under a snapshot directory named
    by a commit hash. That mapping is fastembed's business and it is free to
    change it; matching on the model's last path segment survives that, where
    a hard-coded path would break silently on the next release.

    A tokenizer whose name does not match is NOT accepted: another model's
    vocabulary gives confidently wrong counts, which is worse than counting
    nothing and knowing it.
    """
    stem = EMBEDDING_MODEL.rsplit("/", 1)[-1].lower()
    try:
        candidates = sorted(MODEL_CACHE.glob("**/tokenizer.json"))
    except OSError:
        return None
    for path in candidates:
        if stem in path.as_posix().lower():
            return path
    return None


@dataclass(frozen=True)
class _Rule:
    """The splitting rule in force, resolved once per process."""

    split: MarkdownSplitter
    floor: int
    key: str
    count: Callable[[str], int] | None = None   # tokens, when countable
    cap: MarkdownSplitter | None = None         # re-splitter for oversize


@functools.lru_cache(maxsize=1)
def _rule() -> _Rule:
    lo, hi = CHUNK_CAPACITY
    base = f"md:chr:{lo}-{hi}:m{CHUNK_MERGE_FLOOR_CHARS}"
    path = _tokenizer_file()
    if path is not None:
        try:
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(path))
            cap = MarkdownSplitter.from_huggingface_tokenizer_file(
                str(path), CHUNK_TOKEN_CEILING
            )
        except Exception:
            # Deliberately broad: a truncated download, a tokenizers version
            # that cannot read this file, a permissions problem. None of them
            # is a reason to stop indexing, and all have the same answer —
            # skip the cap and say so in the key.
            pass
        else:
            return _Rule(
                split=MarkdownSplitter(CHUNK_CAPACITY),
                floor=CHUNK_MERGE_FLOOR_CHARS,
                key=f"{base}:t{CHUNK_TOKEN_CEILING}",
                count=lambda text: len(
                    tokenizer.encode(text, add_special_tokens=False).ids
                ),
                cap=cap,
            )
    return _Rule(
        split=MarkdownSplitter(CHUNK_CAPACITY),
        floor=CHUNK_MERGE_FLOOR_CHARS,
        key=f"{base}:t-",
    )


def chunker_key() -> str:
    """Names the splitting rule, for the index to record.

    Two chunkings are as incomparable as two embedding models: the stored
    vectors describe spans this rule would no longer produce. Recording the
    key is what turns "the chunker changed" into a rebuild instead of a
    database holding half of each.
    """
    return _rule().key


def _merge_runts(
    text: str, spans: list[tuple[int, int]], floor: int
) -> list[tuple[int, int]]:
    """Fold a too-small chunk into the one that FOLLOWS it.

    The splitter respects markdown structure, so a heading immediately
    followed by another heading becomes a chunk of its own — about 5% of ours,
    things like ``## Logs`` or ``## Done``. They are harmful in both retrieval
    legs at once: BM25 normalises by length and so rewards a very short
    document, while the chunk's vector is a nearly pure embedding of the
    heading phrase with nothing to dilute it. RRF is rank-based, so winning
    both legs puts such a chunk on top — and a bare heading then displaces the
    section it names.

    Forward, because a heading belongs to the section it opens. The last chunk
    has nothing after it, so it folds backwards instead.
    """
    out = list(spans)
    i = 0
    while len(out) > 1 and i < len(out):
        start, end = out[i]
        if end - start >= floor:
            i += 1
            continue
        if i + 1 < len(out):
            out[i] = (start, out[i + 1][1])
            del out[i + 1]
            # `i` does not advance: the merged block may still be under floor.
        else:
            out[i - 1] = (out[i - 1][0], end)
            del out[i]
            break
    return out


def _cap_tokens(
    text: str, spans: list[tuple[int, int]], rule: _Rule
) -> list[tuple[int, int]]:
    """Re-split any span that would not fit the model's context window.

    Only such a span is touched, so a corpus that never exceeds the window
    comes out of here byte-identical to what went in.
    """
    if rule.count is None or rule.cap is None:
        return spans
    out: list[tuple[int, int]] = []
    for start, end in spans:
        body = text[start:end]
        if rule.count(body) <= CHUNK_TOKEN_CEILING:
            out.append((start, end))
            continue
        out += [
            (start + offset, start + offset + len(part))
            for offset, part in rule.cap.chunk_indices(body)
        ]
    return out


def split_markdown(text: str) -> list[Chunk]:
    """Split markdown into deterministic, heading-aware chunks."""
    rule = _rule()
    spans = [
        (offset, offset + len(part))
        for offset, part in rule.split.chunk_indices(text)
    ]
    spans = _cap_tokens(text, _merge_runts(text, spans, rule.floor), rule)
    return [
        Chunk(
            index=i,
            text=text[start:end],
            heading=_first_heading(text[start:end]),
            start=start,
            end=end,
        )
        for i, (start, end) in enumerate(spans)
    ]
