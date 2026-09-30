import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { parseRef } from "../ref.mjs";

describe("parseRef", () => {
  it("reads a bare number, with or without #", () => {
    assert.deepEqual(parseRef("123"), { repo: null, number: 123 });
    assert.deepEqual(parseRef("#123"), { repo: null, number: 123 });
    assert.deepEqual(parseRef("  #7 "), { repo: null, number: 7 });
  });

  it("reads owner/repo#number", () => {
    assert.deepEqual(parseRef("elastic/kibana#4567"), { repo: "elastic/kibana", number: 4567 });
  });

  it("reads pull and issue URLs, including deeper pages", () => {
    assert.deepEqual(parseRef("https://github.com/acme/widgets/pull/42"), {
      repo: "acme/widgets",
      number: 42,
    });
    assert.deepEqual(parseRef("https://github.com/acme/widgets/pull/42/files#diff-abc"), {
      repo: "acme/widgets",
      number: 42,
    });
    assert.deepEqual(parseRef("https://github.com/acme/my.repo/issues/9"), {
      repo: "acme/my.repo",
      number: 9,
    });
  });

  it("rejects anything else", () => {
    assert.equal(parseRef(""), null);
    assert.equal(parseRef("#"), null);
    assert.equal(parseRef("12a"), null);
    assert.equal(parseRef("acme#12"), null);
    assert.equal(parseRef("https://example.com/acme/widgets/pull/42"), null);
  });
});
