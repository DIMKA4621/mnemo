/**
 * Maps Python code-point offsets (`chunk.start_char`/`end_char`) onto JS
 * string indices (UTF-16 code units). A ported, verbatim algorithm — see
 * `git show a5361db~1:src/webui/static/page-memory.js` (`buildCodePointIndex`/
 * `makeSlicer`), the pre-cutover vanilla console's own implementation of the
 * same fix.
 *
 * Any character above the BMP (an emoji is enough) is one Python code point
 * but two JS UTF-16 units — `text.slice(start_char, end_char)` silently
 * drifts after the first one. `api.py`'s `_as_indexed()` already normalizes
 * newlines and decodes UTF-8 the same way the indexer does, so this is the
 * only remaining offset mismatch a client has to correct for.
 */
export function buildCodePointIndex(text: string): number[] | null {
  if (!/[\uD800-\uDBFF]/.test(text)) return null; // fast path: no surrogate pairs
  const index: number[] = [];
  for (let i = 0; i < text.length; ) {
    index.push(i);
    i += (text.codePointAt(i) ?? 0) > 0xffff ? 2 : 1;
  }
  index.push(text.length);
  return index;
}

export interface Slicer {
  /** Total code-point count — used for the trailing "N characters" label. */
  total: number;
  slice(from: number, to: number): string;
}

export function makeSlicer(text: string): Slicer {
  const index = buildCodePointIndex(text);
  const total = index ? index.length - 1 : text.length;
  return {
    total,
    slice(from: number, to: number): string {
      const a = Math.max(0, Math.min(total, from));
      const b = Math.max(a, Math.min(total, to));
      return index ? text.slice(index[a], index[b]) : text.slice(a, b);
    },
  };
}
