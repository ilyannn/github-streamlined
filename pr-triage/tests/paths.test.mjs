import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { prNumberFromPath, shortPath } from "../paths.mjs";

describe("prNumberFromPath", () => {
  it("reads the number this tool puts in the directory name", () => {
    assert.equal(prNumberFromPath("/repo/.worktrees/pr-1234"), 1234);
    assert.equal(prNumberFromPath("/repo/.worktrees/pr-1234/"), 1234);
  });

  it("is null when the directory is not one of ours", () => {
    assert.equal(prNumberFromPath("/repo"), null);
    assert.equal(prNumberFromPath("/repo/.worktrees/review-pr-stuff"), null);
    assert.equal(prNumberFromPath("/repo/pr-x"), null);
  });

  it("does not read a number from a parent directory", () => {
    assert.equal(prNumberFromPath("/repo/pr-12/nested"), null);
  });
});

describe("shortPath", () => {
  const main = "/Users/me/Code/widgets";

  it("names the main checkout", () => {
    assert.equal(shortPath(main, main), "widgets (main checkout)");
  });

  it("elides the checkout prefix", () => {
    assert.equal(shortPath(`${main}/.worktrees/pr-7`, main), "…/.worktrees/pr-7");
  });

  it("abbreviates a home directory elsewhere on disk", () => {
    assert.equal(shortPath("/Users/me/Code/widgets-side", main), "~/Code/widgets-side");
    assert.equal(shortPath("/home/me/src/thing", main), "~/src/thing");
  });

  it("leaves an unrelated absolute path alone", () => {
    assert.equal(shortPath("/opt/checkouts/thing", main), "/opt/checkouts/thing");
  });

  it("copes with no main checkout", () => {
    assert.equal(shortPath("/Users/me/x", null), "~/x");
  });

  it("does not treat a sibling with the same prefix as inside the checkout", () => {
    // "widgets-side" starts with "widgets" but is not under it.
    assert.equal(shortPath(`${main}-side/pr-1`, main), "~/Code/widgets-side/pr-1");
  });
});
