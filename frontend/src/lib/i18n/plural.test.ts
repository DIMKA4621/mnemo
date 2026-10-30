import { describe, expect, it } from "vitest";

/**
 * The vanilla console's hand-rolled Ukrainian plural rule
 * (`src/webui/static/app.js`'s `PLURAL_RULES.uk`) — copied verbatim, not
 * imported, since it's plain JS living outside `frontend/` and this test's
 * whole point is to check the *behavior* matches, not share the code.
 */
function legacyUkRule(n: number): "one" | "few" | "many" {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "one";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "few";
  return "many";
}

const ukRules = new Intl.PluralRules("uk");

// ICU/CLDR's `uk` rule has a `zero` category that the legacy hand-rolled
// rule never returns (it only ever returns one/few/many) — `zero` in CLDR
// is reserved for languages that have a genuinely distinct zero form, and
// `uk` does not define one, so `Intl.PluralRules('uk').select(0)` cannot
// come back `zero` in practice. Asserted here rather than assumed.
function icuUkCategory(n: number): string {
  return ukRules.select(n);
}

describe("Ukrainian plural rule: legacy hand-rolled vs. ICU/CLDR", () => {
  const values: number[] = [];
  for (let n = 0; n <= 115; n++) values.push(n);
  // A few boundary values further out, past the 0-115 sweep, since the
  // mod100 boundary (11-14) repeats every hundred.
  values.push(200, 211, 212, 214, 215, 221, 1001, 1011, 1021);

  it.each(values)("n=%i matches", (n) => {
    expect(icuUkCategory(n)).toBe(legacyUkRule(n));
  });
});
