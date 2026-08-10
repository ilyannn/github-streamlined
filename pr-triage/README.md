# PR Triage

A local dashboard that answers "which PRs actually need me?" — something the
GitHub PR list can't, because it has no concept of what happened since *your*
last review.

Open PRs are grouped into action buckets:

| Bucket | Meaning |
| --- | --- |
| 🎯 Review now | Others' PRs where your review is requested, you've never looked, code was pushed since your review, or new comments arrived since your last activity |
| 🛠️ Your PRs — act | Anything of yours where the next move is yours: a review verdict to answer, failing CI, conflicts, new comments — or nothing left but the merge |
| 📝 Your drafts | Yours, not open for review yet — each with a **Ready for review** button |
| 🚀 Merge-ready | Other people's PRs you could land: approved, green CI, no conflicts |
| ⏳ Waiting | On the author or other reviewers — nothing for you to do |
| 📝 Drafts | Not ready for review |

## Worktrees

The button on each card is **⎇ Add worktree** when the PR has no worktree yet
and **📂 Open worktree** when it does. Adding one creates
`<checkout>/.worktrees/pr-<number>` and lets `gh` check the PR out there, so
your main checkout keeps whatever you were doing — no stashing, and several
PRs can sit side by side. A PR counts as already having a worktree if its
branch is checked out anywhere, or if that directory is a worktree already.

Either way the folder is then opened with a command of your choice, asked for
the first time you use the button and saved in the browser (`code`, `cursor`,
`zed`, `open`, …). It runs as `<command> <folder>` with no shell involved.

**🌳 Worktrees** in the header (or <kbd>w</kbd>) opens every worktree of the
repo — not only the ones for PRs — searchable by branch, folder, PR number or
PR title, with ↑/↓ and <kbd>Enter</kbd> to open one. Rows show the PR number
and title where the worktree belongs to one of the PRs on screen, and outline
that PR's card as you move through the list. A worktree whose PR is outside
the current query still shows its number, read from the directory name. The
open command lives at the top of that panel, so it is editable wherever you
are.

## Filtering by clicking

Most things on a card are a filter toggle: click the **[scope]** box at the
start of a title, a **label**, an **author** (name or avatar), an **assignee**,
or an **approved** / **changes requested** / **your review requested** badge to
add that term to the query, and click it again to remove it. Applied filters
are outlined, so you can always see what is narrowing the list. **CI** badges
link to the PR's checks instead — GitHub search has no qualifier for them.

To edit rather than filter, hover a card: a ✎ appears after the labels and
after the assignees, opening the same pickers the buttons used to.

The rest of the meta line opens things: **💬** counts everything anyone said,
inline review comments included, with the number of unresolved threads beside
it, and opens the conversation as a tree — review threads with their replies,
reviews and issue comments, each timestamped and linked; **☑** lists the PR's task list, and ticking a box
there edits the pull request body on GitHub; the commit count and the
**+/−** counts link to the commits and the diff.

## Merging

A PR with **auto-merge** already armed says so where the button would be —
outlined rather than solid, because it is state and not something to press.
If a PR somehow has both, both appear.

A PR that GitHub considers mergeable gets a green button under the worktree
one, labelled with the method the repository actually allows — **Squash and
merge**, **Rebase and merge** or **Merge**, read from its settings rather than
assumed. It opens a dialog with what is still outstanding — unresolved review
threads and unticked tasks — above a single large button that merges.

Where a PR cannot be merged, that space lists everything standing in the way
instead — conflicts, failing or running checks, a review verdict, a missing
approval, a stale branch — worst first, so a missing button is never a mystery.
A PR that needs a review *and* is out of date says both: updating the branch
alone would not have got it merged. Nothing here bypasses anything: merging goes through the API, which
enforces the repository's rules, and the admin override flag is never passed.

**✎ Edit commit message** there opens the message GitHub itself would commit,
composed from the repository's own settings (whether the subject comes from
the PR title or a lone commit, and whether the body comes from the PR body,
the commit messages, or nothing). Leave it closed and GitHub composes the
message as usual; open it and what you type is what lands.

Task text and comment bodies render the markdown they contain — code spans,
fenced blocks, emphasis, links, `#1234` references and `@mentions`.

## Cleaning up

The worktree panel has **🧹 Clean up**, which lists worktrees and local
branches belonging to closed or merged PRs. Merged ones start ticked; closed
but unmerged ones do not, since a branch may hold the only copy of that work.
Worktrees with uncommitted changes are shown but cannot be selected, the main
checkout is never offered, and worktrees are removed before their branches so
git does not refuse a checked-out branch.

Section headers collapse and expand (remembered across reloads), the heading
and repo name open the repo and its PR list on github.com, and ✕ resets the
query.

| Key | |
| --- | --- |
| <kbd>/</kbd> | Focus the search box |
| <kbd>r</kbd> | Refresh now |
| <kbd>Enter</kbd> | Apply the query (it persists across reloads) |
| <kbd>Esc</kbd> | Close a popover, or leave the search box |

## Requirements

Python 3.11+ and an authenticated `gh` CLI. No other dependencies.

## Run

From this repository, naming the checkout you want to triage:

```bash
just run ~/Code/some-repo
```

`just run` on its own triages this repository. Either way it is a wrapper
around running the script from inside the target checkout, which you can do
directly — the tool triages whichever repo it runs *from*, not the one it
lives in:

```bash
cd ~/Code/some-repo && python3 ~/Code/github-streamlined/pr-triage/pr_triage.py
```

Then open <http://127.0.0.1:8642>. The search box accepts GitHub search
syntax, e.g. `is:open label:"my-team"`; press Enter to apply, and the query
sticks across reloads. The page auto-refreshes every 2 minutes.

It opens on everything that touches you — `is:open involves:@me | is:open
review-requested:@me`. Two searches, because `involves:` covers what you
authored, were assigned, commented on or were mentioned in, but *not* what is
merely waiting on your review, and GitHub issue search has no OR. A `|`
anywhere in the query asks for each side to be searched and the results merged,
deduplicated by number. `is:open` on its own still gives you the whole repo.

## Reloading

Left running, it keeps up with itself. Editing its Python restarts the process
in place; editing the HTML or a module changes a fingerprint the page notices on
its next refresh, and it reloads itself. So a dashboard open for days is never
quietly running week-old code. Set `PR_TRIAGE_RELOAD=0` to hold it still.

It will not restart into code that cannot run. A burst of saves settles first,
so a series of edits restarts once; then the new source has to parse *and*
survive being imported in a subprocess. If it fails either, the old process
keeps serving and says why, and waits for the next save. Losing the server to a
half-written file would be worse than running code a minute old — which
matters if someone else is editing while you have it open.

## Configuration

| Env var | Default | |
| --- | --- | --- |
| `PR_TRIAGE_PORT` | `8642` | Listen port (localhost only) |
| `PR_TRIAGE_REPO` | detected via `gh repo view` in cwd | `owner/name` to triage |
| `PR_TRIAGE_CHECKOUT` | primary worktree of cwd's repo | Repo the worktree button operates on |
| `PR_TRIAGE_WORKTREES` | `<checkout>/.worktrees` | Where new worktrees are created |
| `PR_TRIAGE_QUERY` | `is:open involves:@me \| is:open review-requested:@me` | Default search query; `\|` merges several searches |
| `PR_TRIAGE_RELOAD` | `1` | Restart when the source changes; `0` to disable |

## Security model

The server binds to `127.0.0.1` only. All GitHub access goes through your
authenticated `gh` CLI; the server itself holds no tokens. Commands are built
as argument lists and never go through a shell, so nothing in a branch name,
label, or path can turn into shell syntax.

Anything destructive is checked against a freshly computed list on the server:
`/api/open` only accepts a path git reports as a worktree, and cleanup only
removes worktrees and branches it would itself have offered — a request cannot
name an arbitrary directory or branch.

Because these endpoints run `git`, `gh`, and your open command, `POST`s must
carry an `X-PR-Triage` header and may not come from another origin. The header
is not CORS-safelisted, so a page on any other site would need a preflight this
server never answers.

## Tests

From the repository root:

```bash
just tests
```

`just test-py` covers the server and the triage logic; `just test-js` covers
[`query.mjs`](query.mjs), the query-string helpers behind the filter buttons.
