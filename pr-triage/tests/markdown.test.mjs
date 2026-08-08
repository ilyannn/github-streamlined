import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { escapeHtml, render } from "../markdown.mjs";

const REPO = "acme/widgets";

describe("escaping", () => {
  it("escapes HTML in plain text", () => {
    assert.equal(render("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;");
  });

  it("escapes HTML inside code spans", () => {
    assert.equal(render("`<b>&</b>`"), "<code>&lt;b&gt;&amp;&lt;/b&gt;</code>");
  });

  it("escapes an ampersand once", () => {
    assert.equal(escapeHtml("a & b"), "a &amp; b");
    assert.equal(render("a & b"), "a &amp; b");
  });

  it("returns empty for nothing", () => {
    assert.equal(render(""), "");
    assert.equal(render(null), "");
    assert.equal(render(undefined), "");
  });
});

describe("code", () => {
  it("renders an inline code span", () => {
    assert.equal(
      render("Confirm `docker.elastic.co/git-poll:v0.5.1` exists"),
      "Confirm <code>docker.elastic.co/git-poll:v0.5.1</code> exists",
    );
  });

  it("renders several spans on one line", () => {
    assert.equal(render("`a` and `b`"), "<code>a</code> and <code>b</code>");
  });

  it("leaves markup inside code alone", () => {
    assert.equal(render("`a **b** c`"), "<code>a **b** c</code>");
    assert.equal(render("`see #12`", REPO), "<code>see #12</code>");
  });

  it("renders a fenced block and drops its info string", () => {
    assert.equal(render("```bash\njust check\n```"), "<pre><code>just check</code></pre>");
  });

  it("does not treat a fence as three inline spans", () => {
    const out = render("before\n```\na `b` c\n```\nafter");
    assert.match(out, /<pre><code>a `b` c<\/code><\/pre>/);
    assert.match(out, /^before\n/);
    assert.match(out, /\nafter$/);
  });
});

describe("emphasis", () => {
  it("renders bold and italic", () => {
    assert.equal(render("**bold** and *italic*"), "<strong>bold</strong> and <em>italic</em>");
    assert.equal(render("_underscored_"), "<em>underscored</em>");
    assert.equal(render("~~gone~~"), "<del>gone</del>");
  });

  it("leaves a snake_case word alone", () => {
    assert.equal(render("service_name_here"), "service_name_here");
  });

  it("leaves a lone asterisk alone", () => {
    assert.equal(render("ELASTIC_APM_*"), "ELASTIC_APM_*");
  });
});

describe("links", () => {
  it("renders a labelled link", () => {
    assert.equal(
      render("see [the docs](https://example.invalid/x)"),
      'see <a href="https://example.invalid/x" target="_blank" rel="noreferrer">the docs</a>',
    );
  });

  it("links a bare URL", () => {
    assert.match(render("go to https://example.invalid/a"), /<a href="https:\/\/example\.invalid\/a"/);
  });

  it("does not double-link a labelled link", () => {
    const out = render("[x](https://example.invalid/a)");
    assert.equal(out.match(/<a /g).length, 1);
  });

  it("refuses a javascript: URL", () => {
    const out = render("[click](javascript:alert(1))");
    assert.doesNotMatch(out, /<a /);
    assert.match(out, /javascript:alert\(1\)/);
  });

  it("links issue references when a repo is known", () => {
    assert.equal(
      render("fixes #1223", REPO),
      'fixes <a href="https://github.com/acme/widgets/pull/1223" target="_blank" ' +
        'rel="noreferrer">#1223</a>',
    );
  });

  it("leaves references alone without a repo", () => {
    assert.equal(render("fixes #1223"), "fixes #1223");
  });

  it("links a mention", () => {
    assert.match(render("thanks @octocat"), /href="https:\/\/github\.com\/octocat"/);
  });

  it("does not treat an email-ish string as a mention", () => {
    assert.doesNotMatch(render("write to me@example.invalid"), /<a /);
  });
});
