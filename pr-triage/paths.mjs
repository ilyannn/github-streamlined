// Pure helpers for showing worktree paths, kept out of index.html so
// `node --test` can exercise them.

/** The PR number a worktree directory was made for, or null. */
export function prNumberFromPath(path) {
  const match = /\/pr-(\d+)\/?$/.exec(path);
  return match ? Number(match[1]) : null;
}

/** Shorten a worktree path for display, relative to the checkout it belongs to. */
export function shortPath(path, main) {
  if (main && path === main) return `${main.split("/").pop()} (main checkout)`;
  if (main && path.startsWith(main + "/")) return "…/" + path.slice(main.length + 1);
  return path.replace(/^\/(?:Users|home)\/[^/]+/, "~");
}
