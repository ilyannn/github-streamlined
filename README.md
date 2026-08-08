# github-streamlined

Small local tools that streamline GitHub workflows the github.com UI is bad at.

Design rules for tools in this collection:

- **Local-first** — runs on your machine, binds to localhost, no deployment.
- **Zero dependencies** — Python stdlib (or equally boring), plus the
  authenticated `gh` CLI for all GitHub access. Nothing to install or update.
- **Repo-agnostic** — target repo detected from the cwd, overridable via env.

## Tools

| Tool | What it does |
| --- | --- |
| [pr-triage](pr-triage/) | Dashboard that buckets open PRs by what actually needs *you* (review now / act on yours / merge-ready / waiting / drafts), with one-click checkout, relabel, and assign |

## Development

The toolchain is pinned in [`.tool-versions`](.tool-versions) and installed by
[mise](https://mise.jdx.dev), both locally and in CI:

```bash
mise install
```

Then:

```bash
just          # list recipes
just tests    # run the test suite
just lint     # ruff + taplo
just check    # everything CI enforces
```

## License

[MIT](LICENSE)
