import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { hasTerm, term, toggleTerm, tokenize } from "../query.mjs";

describe("tokenize", () => {
  it("returns an empty list for an empty query", () => {
    assert.deepEqual(tokenize(""), []);
    assert.deepEqual(tokenize("   "), []);
  });

  it("splits on whitespace", () => {
    assert.deepEqual(tokenize("is:open is:pr"), ["is:open", "is:pr"]);
  });

  it("keeps a quoted value with a colon in one token", () => {
    assert.deepEqual(tokenize('is:open label:"wg:toolbox"'), ["is:open", 'label:"wg:toolbox"']);
  });

  it("keeps a quoted value with spaces in one token", () => {
    assert.deepEqual(tokenize('label:"good first issue" is:open'), [
      'label:"good first issue"',
      "is:open",
    ]);
  });

  it("collapses runs of whitespace", () => {
    assert.deepEqual(tokenize("  is:open   is:pr "), ["is:open", "is:pr"]);
  });
});

describe("term", () => {
  it("leaves simple values unquoted", () => {
    assert.equal(term("author", "octocat"), "author:octocat");
    assert.equal(term("label", "bug"), "label:bug");
    assert.equal(term("label", "app.v1-x/y"), "label:app.v1-x/y");
  });

  it("quotes values containing a colon", () => {
    assert.equal(term("label", "wg:toolbox"), 'label:"wg:toolbox"');
  });

  it("quotes values containing spaces", () => {
    assert.equal(term("label", "good first issue"), 'label:"good first issue"');
  });

  it("drops embedded quotes rather than emitting an unbalanced query", () => {
    assert.equal(term("label", 'we"ird'), "label:weird");
  });
});

describe("hasTerm", () => {
  it("finds an exact token", () => {
    assert.equal(hasTerm('is:open label:"wg:toolbox"', 'label:"wg:toolbox"'), true);
  });

  it("ignores case", () => {
    assert.equal(hasTerm("is:open AUTHOR:octocat", "author:octocat"), true);
  });

  it("does not match a prefix of another token", () => {
    assert.equal(hasTerm("label:bugfix", "label:bug"), false);
    assert.equal(hasTerm('label:"wg:toolbox-extra"', 'label:"wg:toolbox"'), false);
  });

  it("is false for an empty query", () => {
    assert.equal(hasTerm("", "label:bug"), false);
  });
});

describe("toggleTerm", () => {
  it("adds a term that is absent", () => {
    assert.equal(toggleTerm("is:open", "author:octocat"), "is:open author:octocat");
  });

  it("removes a term that is present", () => {
    assert.equal(toggleTerm("is:open author:octocat", "author:octocat"), "is:open");
  });

  it("round-trips back to the original query", () => {
    const start = 'is:open label:"wg:toolbox"';
    const added = toggleTerm(start, "author:octocat");
    assert.equal(toggleTerm(added, "author:octocat"), start);
  });

  it("preserves the order of the other tokens", () => {
    assert.equal(
      toggleTerm('is:open label:"a b" is:pr', 'label:"a b"'),
      "is:open is:pr",
    );
  });

  it("adds to an empty query without leading whitespace", () => {
    assert.equal(toggleTerm("", "label:bug"), "label:bug");
    assert.equal(toggleTerm("   ", "label:bug"), "label:bug");
  });

  it("removes every copy of a duplicated term", () => {
    assert.equal(toggleTerm("is:open is:open label:bug", "is:open"), "label:bug");
  });
});
