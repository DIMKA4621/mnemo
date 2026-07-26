"""Heading-aware markdown chunking via semantic-text-splitter.

We do not reinvent splitting; we wrap the Rust-backed MarkdownSplitter.
A file smaller than the minimum capacity naturally yields a single chunk.

Each chunk carries its character span in the source file. Those offsets are
stored in the index so the UI can draw real chunk boundaries over the raw
``.md`` instead of re-splitting it — a second splitting path is exactly the
kind of drift we avoid. The splitting rule (CHUNK_CAPACITY) is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from semantic_text_splitter import MarkdownSplitter

from .config import CHUNK_CAPACITY


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


def split_markdown(text: str) -> list[Chunk]:
    """Split markdown into deterministic, heading-aware chunks."""
    splitter = MarkdownSplitter(CHUNK_CAPACITY)
    return [
        Chunk(
            index=i,
            text=part,
            heading=_first_heading(part),
            start=offset,
            end=offset + len(part),
        )
        for i, (offset, part) in enumerate(splitter.chunk_indices(text))
    ]
