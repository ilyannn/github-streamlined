# PR Triage

A local dashboard that answers "which PRs actually need me?" — something the
GitHub PR list can't, because it has no concept of what happened since *your*
last review.

Open PRs are grouped into action buckets:

| Bucket | Meaning |
| --- | --- |
| 🎯 Review now | Others' PRs where your review is requested, you've never looked, code was pushed since your review, or new comments arrived since your last activity |
| 🛠️ Your PRs — act | Your PRs with changes requested, failing CI, or new comments |
| 🚀 Merge-ready | Approved with green CI |
| ⏳ Waiting | On the author or other reviewers — nothing for you to do |
| 📝 Drafts | Not ready for review |

Per-PR actions: **⎇ Checkout** (runs `gh pr checkout` in your primary
checkout, refusing if it has uncommitted tracked changes), **🏷 Labels**
(toggle any repo label), **👤 Assign** (one-click assign-me, or add/remove
any login).

## Requirements

Python 3.11+ and an authenticated `gh` CLI. No other dependencies.

## Run

From inside a checkout of the repo you want to triage:

```bash
python3 pr-triage/pr_triage.py
```

(Use the full path to `pr_triage.py` if your cwd is the target repo — the
tool triages whichever repo you run it *from*, not the one it lives in.)

Then open <http://127.0.0.1:8642>. The search box accepts GitHub search
syntax, e.g. `is:open label:"my-team"`; press Enter to apply, and the query
sticks across reloads. The page auto-refreshes every 2 minutes.

## Configuration

| Env var | Default | |
| --- | --- | --- |
| `PR_TRIAGE_PORT` | `8642` | Listen port (localhost only) |
| `PR_TRIAGE_REPO` | detected via `gh repo view` in cwd | `owner/name` to triage |
| `PR_TRIAGE_CHECKOUT` | primary worktree of cwd's repo | Directory the Checkout button operates on |
| `PR_TRIAGE_QUERY` | `is:open` | Default search query |

## Security model

The server binds to `127.0.0.1` only. All GitHub access goes through your
authenticated `gh` CLI; the server itself holds no tokens. Mutating endpoints
run fixed `gh`/`git` commands with validated arguments (no shell
interpolation), and Checkout refuses to touch a checkout with uncommitted
tracked changes.

## Tests

From the repository root:

```bash
just tests
```
