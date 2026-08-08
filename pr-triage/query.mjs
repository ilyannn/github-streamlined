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

export function hasTerm(query, wanted) {
  const needle = wanted.toLowerCase();
  return tokenize(query).some((token) => token.toLowerCase() === needle);
}

/** Add `wanted` to the query, or remove it if already present. */
export function toggleTerm(query, wanted) {
  const needle = wanted.toLowerCase();
  const tokens = tokenize(query);
  const kept = tokens.filter((token) => token.toLowerCase() !== needle);
  if (kept.length !== tokens.length) return kept.join(" ");
  return [...tokens, wanted].join(" ");
}
