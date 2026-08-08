// The small slice of GitHub-flavoured markdown that shows up in PR bodies and
// review comments. Not a general markdown engine: it renders what a task line
// or a comment paragraph actually contains, and escapes everything else.
//
// Everything is HTML-escaped before any markup is added, so no text from
// GitHub can introduce tags of its own.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

const LINK_ATTRS = 'target="_blank" rel="noreferrer"';

function inline(escaped, repo) {
  let out = escaped;
  // [label](url) before bare URLs, so a labelled link is not linked twice.
  out = out.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, label, href) => `<a href="${href}" ${LINK_ATTRS}>${label}</a>`,
  );
  out = out.replace(
    /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    (_, before, url) => `${before}<a href="${url}" ${LINK_ATTRS}>${url}</a>`,
  );
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|\W)\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  out = out.replace(/(^|\W)_([^_\s][^_]*)_(?=\W|$)/g, "$1<em>$2</em>");
  out = out.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  if (repo) {
    out = out.replace(
      /(^|[\s(])#(\d+)\b/g,
      (_, before, number) =>
        `${before}<a href="https://github.com/${repo}/pull/${number}" ${LINK_ATTRS}>#${number}</a>`,
    );
  }
  out = out.replace(
    /(^|[\s(])@([A-Za-z0-9][A-Za-z0-9-]{0,38})\b/g,
    (_, before, login) =>
      `${before}<a href="https://github.com/${login}" ${LINK_ATTRS}>@${login}</a>`,
  );
  return out;
}

/**
 * Render markdown to HTML.
 *
 * Fenced blocks and inline code are taken out first so nothing inside them is
 * treated as markup — a comment full of shell snippets is the common case.
 */
export function render(text, repo) {
  const source = String(text ?? "");
  if (!source) return "";
  return source
    .split(/(```[\s\S]*?```)/g)
    .map((chunk) => {
      if (chunk.startsWith("```") && chunk.endsWith("```") && chunk.length > 5) {
        // Drop the info string ("```bash") along with the opening fence.
        const body = chunk.slice(3, -3).replace(/^[^\n]*\n?/, "");
        return `<pre><code>${escapeHtml(body.replace(/\n$/, ""))}</code></pre>`;
      }
      return chunk
        .split(/(`[^`\n]+`)/g)
        .map((part) =>
          part.startsWith("`") && part.endsWith("`") && part.length > 2
            ? `<code>${escapeHtml(part.slice(1, -1))}</code>`
            : inline(escapeHtml(part), repo),
        )
        .join("");
    })
    .join("");
}
