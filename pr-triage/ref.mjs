// Pure helper for the `:` jump box, kept out of index.html so `node --test`
// can exercise it. The accepted forms follow BetterGithub's jump box (MIT; see
// THIRD_PARTY_LICENSES.md).

const URL_RE = /^https?:\/\/(?:www\.)?github\.com\/([\w.-]+\/[\w.-]+)\/(?:pull|issues)\/(\d+)/i;
const QUALIFIED_RE = /^([\w.-]+\/[\w.-]+)#(\d+)$/;
const BARE_RE = /^#?(\d+)$/;

/**
 * What a typed reference points at: `123`, `#123`, `owner/repo#123`, or a
 * github.com pull or issue URL. `repo` is null when the reference does not
 * name one. Returns null for anything else.
 */
export function parseRef(text) {
  const value = text.trim();
  let match = URL_RE.exec(value) || QUALIFIED_RE.exec(value);
  if (match) return { repo: match[1], number: Number(match[2]) };
  match = BARE_RE.exec(value);
  return match ? { repo: null, number: Number(match[1]) } : null;
}
