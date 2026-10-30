/**
 * Merges `override` onto `base`, recursing into plain objects and taking
 * `override`'s value everywhere else. Used to build each locale's message
 * tree as English-plus-Ukrainian-overrides — replicating the vanilla
 * console's `t()` fallback ("missing from `uk`? use `en`.") at the data
 * level instead of per-lookup, since next-intl has no built-in cross-locale
 * fallback of its own.
 */
export function deepMerge<T extends Record<string, unknown>>(base: T, override: Partial<T>): T {
  const result: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(override)) {
    const baseValue = result[key];
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      baseValue &&
      typeof baseValue === "object" &&
      !Array.isArray(baseValue)
    ) {
      result[key] = deepMerge(baseValue as Record<string, unknown>, value as Record<string, unknown>);
    } else if (value !== undefined) {
      result[key] = value;
    }
  }
  return result as T;
}
