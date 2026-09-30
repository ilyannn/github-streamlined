# Third-party licenses

## BetterGithub

Parts of pr-triage are adapted from
[BetterGithub](https://github.com/stanek-michal/BetterGithub) (`bgh`):

- the **since your review** diff — fetching both sides as blobs and diffing
  them locally so it survives force-pushes (`fetch_interdiff`, `fetch_blobs`,
  `make_patch` in `pr_triage.py`, after `bgh/app.py`, `bgh/sync.py` and
  `bgh/diff.py`);
- the default lists of **bot accounts and CI commands** that do not count as
  new comments (`IGNORED_AUTHORS`, `COMMAND_RE`, after `config.example.toml`);
- the reference forms the **`:` jump box** accepts (`ref.mjs`).

The ideas behind `j`/`k` navigation and filtering PRs by changed paths come
from there too, though the code for those is new.

```
MIT License

Copyright (c) 2026 Michal Stanek

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
