// Pure helpers for editing a GitHub search query string.
//
// Lives outside index.html so `node --test` can exercise it: the quoting and
// token-matching rules below are easy to break silently from the UI.

// A token is a run of non-space characters, except that a quoted section may
// contain spaces — so `label:"wg:toolbox"` stays one token.
const TOKEN_RE = /(?:[^\s"]+|"[^"]*")+/g;

// Values made only of these characters need no quoting in GitHub search.
const BARE_RE = /^[A-Za-z0-9_./-]+$/;

export function tokenize(query) {
  return query.match(TOKEN_RE) ?? [];
}

/** Build a `kind:value` term, quoting the value when GitHub needs it. */
export function term(kind, value) {
  // GitHub search has no escape for a double quote inside a quoted value, and
  // an unbalanced quote would swallow the rest of the query, so drop them.
  const safe = value.replace(/"/g, "");
  return BARE_RE.test(safe) ? `${kind}:${safe}` : `${kind}:"${safe}"`;
}

// `wanted` is itself a query fragment, so a filter can be more than one token:
// a scope box adds `"[infra]" in:title`, a label adds just `label:bug`.

export function hasTerms(query, wanted) {
  const have = new Set(tokenize(query).map((token) => token.toLowerCase()));
  const needles = tokenize(wanted).map((token) => token.toLowerCase());
  return needles.length > 0 && needles.every((needle) => have.has(needle));
}

/** Add every token of `wanted` to the query, or remove them all if all present. */
export function toggleTerms(query, wanted) {
  const tokens = tokenize(query);
  const needles = tokenize(wanted);
  const lowered = needles.map((token) => token.toLowerCase());
  if (hasTerms(query, wanted)) {
    return tokens.filter((token) => !lowered.includes(token.toLowerCase())).join(" ");
  }
  const have = new Set(tokens.map((token) => token.toLowerCase()));
  return [...tokens, ...needles.filter((token) => !have.has(token.toLowerCase()))].join(" ");
}
