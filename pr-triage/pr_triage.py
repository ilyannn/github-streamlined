#!/usr/bin/env python3
"""Local PR triage dashboard.

Answers the question GitHub's PR list can't: "which PRs actually need me?"
Groups open PRs into action buckets (review now / your PRs needing action /
merge-ready / waiting / drafts) based on what happened since *your* last
review or comment, and offers one-click checkout, relabel, and assign.

Zero dependencies beyond the Python stdlib and an authenticated `gh` CLI.

Run it from inside a checkout of the repo you want to triage:
    python3 <path-to>/pr-triage/pr_triage.py
Then open http://127.0.0.1:8642

The target repo and the checkout the ⎇ button operates on are detected from
the current working directory; override with env vars to run from anywhere:
    PR_TRIAGE_PORT      port to listen on           (default 8642)
    PR_TRIAGE_REPO      owner/name                  (default: `gh repo view` in cwd)
    PR_TRIAGE_CHECKOUT  dir for the Checkout button (default: primary worktree of cwd)
    PR_TRIAGE_WORKTREES where worktrees are created (default: <checkout>/.worktrees)
    PR_TRIAGE_QUERY     default search query        (default: is:open)
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PORT = int(os.getenv("PR_TRIAGE_PORT", "8642"))
DEFAULT_QUERY = os.getenv("PR_TRIAGE_QUERY", "is:open")

# The only files served off disk, so a path can never escape this directory.
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/query.mjs": ("query.mjs", "text/javascript; charset=utf-8"),
    "/paths.mjs": ("paths.mjs", "text/javascript; charset=utf-8"),
    "/markdown.mjs": ("markdown.mjs", "text/javascript; charset=utf-8"),
}

ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

LOGIN_RE = re.compile(r"^[A-Za-z0-9-]{1,39}$")
# Label names may contain letters, digits, spaces and common punctuation,
# but never commas (comma is gh's list separator).
LABEL_RE = re.compile(r"^[^,]{1,100}$")

GRAPHQL_QUERY = """
query($q: String!) {
  viewer { login avatarUrl }
  search(query: $q, type: ISSUE, first: 50) {
    issueCount
    nodes {
      ... on PullRequest {
        number title url body isDraft reviewDecision createdAt updatedAt
        headRefName additions deletions mergeable mergeStateStatus
        autoMergeRequest { enabledAt mergeMethod enabledBy { login } }
        author { login avatarUrl }
        labels(first: 20) { nodes { name color } }
        assignees(first: 10) { nodes { login } }
        reviewRequests(first: 10) {
          nodes { requestedReviewer {
            __typename
            ... on User { login }
            ... on Team { name }
          } }
        }
        commits(last: 1) { totalCount nodes { commit { committedDate statusCheckRollup { state } } } }
        reviews(last: 100) { nodes { author { login } state submittedAt } }
        comments(last: 100) { totalCount nodes { author { login } createdAt } }
      }
    }
  }
}
"""


def sh(args, cwd=None, timeout=90):
    proc = subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def spawn(args):
    """Start a process and leave it running — an editor outlives this request."""
    subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )


def detect_repo():
    repo = os.getenv("PR_TRIAGE_REPO", "")
    if repo:
        return repo
    rc, out, err = sh(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    if rc != 0:
        raise SystemExit(
            "Cannot detect a repo from the current directory.\n"
            "Run from inside a checkout, or set PR_TRIAGE_REPO=owner/name.\n"
            f"({err.strip()})"
        )
    return out.strip()


def detect_main_checkout():
    """The primary worktree of the cwd's repo — where the Checkout button operates."""
    override = os.getenv("PR_TRIAGE_CHECKOUT", "")
    if override:
        return str(Path(override).expanduser().resolve())
    rc, out, _ = sh(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if rc != 0:
        return None
    return str(Path(out.strip()).parent)


# Resolved in main() so importing this module (e.g. from tests) has no side
# effects and never shells out.
REPO = ""
MAIN_CHECKOUT = None


def ts(iso):
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# Groups: the bullet and opening bracket, the mark, the closing bracket, the text.
TASK_RE = re.compile(r"^(\s*[-*]\s+\[)( |x|X)(\]\s*)(.*)$", re.MULTILINE)


def parse_tasks(body):
    return [
        {"index": i, "done": found.group(2) in "xX", "text": found.group(4).strip()}
        for i, found in enumerate(TASK_RE.finditer(body or ""))
    ]


def count_tasks(body):
    tasks = parse_tasks(body)
    return sum(1 for t in tasks if t["done"]), len(tasks)


def toggle_task(body, index, done):
    """Return the body with checkbox `index` set, leaving everything else byte for byte."""
    seen = 0

    def flip(found):
        nonlocal seen
        mark = ("x" if done else " ") if seen == index else found.group(2)
        seen += 1
        return found.group(1) + mark + found.group(3) + found.group(4)

    updated = TASK_RE.sub(flip, body or "")
    if index >= seen:
        raise RuntimeError(f"No task #{index} on this pull request")
    return updated


def pr_body(number):
    rc, out, err = sh(
        ["gh", "pr", "view", str(number), "--repo", REPO, "--json", "body", "--jq", ".body"]
    )
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip())
    return out


def fetch_tasks(number):
    return {"number": number, "tasks": parse_tasks(pr_body(number))}


def set_task(number, index, done):
    updated = toggle_task(pr_body(number), int(index), bool(done))
    # Through a file rather than an argument: a PR body can be long, and this
    # keeps every newline and backslash exactly as GitHub had it.
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
        handle.write(updated)
        path = handle.name
    try:
        rc, out, err = sh(["gh", "pr", "edit", str(number), "--repo", REPO, "--body-file", path])
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
    finally:
        os.unlink(path)
    return {"number": number, "tasks": parse_tasks(updated)}


def auto_merge(pr):
    """Auto-merge, if it is armed: GitHub merges this one once it is allowed to."""
    request = pr.get("autoMergeRequest")
    if not request:
        return None
    return {
        "method": (request.get("mergeMethod") or "").lower(),
        "at": request.get("enabledAt"),
        "by": (request.get("enabledBy") or {}).get("login"),
    }


def classify(pr, me):
    """Return (bucket, badges) for a PR from the viewer's perspective."""
    badges = []
    author = (pr.get("author") or {}).get("login")
    mine = author == me
    draft = pr["isDraft"]
    decision = pr.get("reviewDecision")  # APPROVED | CHANGES_REQUESTED | REVIEW_REQUIRED | None

    commits = pr["commits"]["nodes"]
    commit = commits[0]["commit"] if commits else {}
    rollup = commit.get("statusCheckRollup") or {}
    ci = rollup.get("state")  # SUCCESS | FAILURE | ERROR | PENDING | EXPECTED | None
    last_commit = ts(commit.get("committedDate"))

    reviews = [r for r in pr["reviews"]["nodes"] if r.get("author")]
    comments = [c for c in pr["comments"]["nodes"] if c.get("author")]

    my_events = [ts(r["submittedAt"]) for r in reviews if r["author"]["login"] == me] + [
        ts(c["createdAt"]) for c in comments if c["author"]["login"] == me
    ]
    my_last = max(my_events) if my_events else None
    my_review_times = [ts(r["submittedAt"]) for r in reviews if r["author"]["login"] == me]
    my_last_review = max(my_review_times) if my_review_times else None

    others_new = 0
    if my_last:
        others_new = sum(
            1 for c in comments if c["author"]["login"] != me and ts(c["createdAt"]) > my_last
        )
        others_new += sum(
            1 for r in reviews if r["author"]["login"] != me and ts(r["submittedAt"]) > my_last
        )

    requested_of_me = any(
        (n.get("requestedReviewer") or {}).get("login") == me for n in pr["reviewRequests"]["nodes"]
    )

    pushed_since_review = bool(my_last_review and last_commit and last_commit > my_last_review)

    if ci in ("FAILURE", "ERROR"):
        badges.append({"text": "CI failing", "kind": "danger"})
    elif ci == "PENDING":
        badges.append({"text": "CI running", "kind": "muted"})
    if pr.get("mergeable") == "CONFLICTING":
        # Otherwise an approved, green PR with no merge button looks broken.
        badges.append({"text": "conflicts", "kind": "danger"})
    if decision == "CHANGES_REQUESTED":
        badges.append({"text": "changes requested", "kind": "danger"})
    elif decision == "APPROVED":
        badges.append({"text": "approved", "kind": "ok"})
    if requested_of_me:
        badges.append({"text": "your review requested", "kind": "info"})
    if pushed_since_review and not mine:
        badges.append({"text": "pushed since your review", "kind": "warn"})
    if others_new:
        plural = "s" if others_new > 1 else ""
        badges.append({"text": f"{others_new} new comment{plural}", "kind": "warn"})
    if not mine and not my_last and not draft:
        badges.append({"text": "not seen by you", "kind": "info"})

    ci_ok = ci in ("SUCCESS", "EXPECTED", None)
    conflicting = pr.get("mergeable") == "CONFLICTING"
    # Conflicts disqualify a PR however green and approved it is: nobody can
    # merge it until the branch is rebased, so it is work, not a candidate.
    landable = decision == "APPROVED" and ci_ok and not conflicting

    if draft:
        bucket = "drafts"
    elif mine:
        # Merge-ready wins over "act": a PR of yours that is approved and green
        # belongs with the ones you can land, even if a comment arrived since.
        # The badge still says so.
        if landable:
            bucket = "merge_ready"
        elif (
            # Approved but not landable yet — CI still running, say. Reviewers
            # are done, so the next move is yours; it is not waiting on anyone.
            decision == "APPROVED"
            or decision == "CHANGES_REQUESTED"
            or ci in ("FAILURE", "ERROR")
            or conflicting
            or others_new
        ):
            bucket = "yours_act"
        else:
            # Nobody has reviewed it yet: genuinely waiting on other people.
            bucket = "waiting"
    else:
        needs_me = (
            requested_of_me
            or pushed_since_review
            or others_new
            or (not my_last and decision in (None, "REVIEW_REQUIRED"))
        )
        if landable:
            bucket = "merge_ready"
        elif needs_me:
            bucket = "review"
        else:
            # Someone else's conflicted PR waits on its author to rebase.
            bucket = "waiting"

    return bucket, badges


def run_search(search):
    rc, out, err = sh(["gh", "api", "graphql", "-f", f"query={GRAPHQL_QUERY}", "-f", f"q={search}"])
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip())
    return json.loads(out)["data"]


def waiting_on_mergeability(data):
    """True while GitHub still owes us a mergeable verdict for a candidate.

    GitHub computes mergeability lazily: the first request for it comes back
    UNKNOWN and kicks off the work. Without a second look, the merge button
    would only ever appear on the refresh after the one you asked for.
    """
    return any(
        pr
        and pr.get("mergeable") == "UNKNOWN"
        and pr.get("reviewDecision") == "APPROVED"
        and not pr["isDraft"]
        for pr in data["search"]["nodes"]
    )


def fetch_prs(user_query):
    search = f"repo:{REPO} is:pr {user_query}"
    data = run_search(search)
    if waiting_on_mergeability(data):
        time.sleep(1.5)
        data = run_search(search)
    me = data["viewer"]["login"]
    # One listing for the whole page, so each row can say whether its worktree
    # is already there ("Open") or still has to be made ("Add").
    trees = list_worktrees()

    prs = []
    for pr in data["search"]["nodes"]:
        if not pr:  # non-PR search results come back as empty objects
            continue
        bucket, badges = classify(pr, me)
        done, total = count_tasks(pr.get("body"))
        found = find_worktree(trees, pr["number"], pr["headRefName"])
        prs.append(
            {
                "number": pr["number"],
                "title": pr["title"],
                "url": pr["url"],
                "author": (pr.get("author") or {}).get("login", "ghost"),
                "avatarUrl": (pr.get("author") or {}).get("avatarUrl", ""),
                "isDraft": pr["isDraft"],
                "decision": pr.get("reviewDecision"),
                "createdAt": pr["createdAt"],
                "updatedAt": pr["updatedAt"],
                "headRefName": pr["headRefName"],
                "additions": pr["additions"],
                "deletions": pr["deletions"],
                "labels": pr["labels"]["nodes"],
                "assignees": [a["login"] for a in pr["assignees"]["nodes"]],
                "commentCount": pr["comments"]["totalCount"],
                "commitCount": pr["commits"]["totalCount"],
                "canMerge": (
                    pr.get("mergeable") == "MERGEABLE"
                    and pr.get("mergeStateStatus") in MERGEABLE_STATES
                    # mergeStateStatus alone is not enough: it reports a single
                    # reason, so a PR that is both out of date and blocked by a
                    # changes-requested review comes back BEHIND, not BLOCKED.
                    and pr.get("reviewDecision") == "APPROVED"
                    and not pr["isDraft"]
                ),
                "mergeState": pr.get("mergeStateStatus"),
                "autoMerge": auto_merge(pr),
                "tasksDone": done,
                "tasksTotal": total,
                "bucket": bucket,
                "badges": badges,
                "worktree": found[0] if found else None,
            }
        )
    return {
        "viewer": data["viewer"],
        "repo": REPO,
        "query": user_query,
        "issueCount": data["search"]["issueCount"],
        "mainCheckout": MAIN_CHECKOUT,
        "worktreeCount": len(trees),
        "mergeMethod": merge_method(),
        "prs": prs,
    }


def parse_worktrees(porcelain):
    """[(path, branch)] from `git worktree list --porcelain`; branch is None if detached."""
    trees, path, branch = [], None, None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            if path is not None:
                trees.append((path, branch))
            path, branch = line.removeprefix("worktree "), None
        elif line.startswith("branch "):
            branch = line.removeprefix("branch ").removeprefix("refs/heads/")
    if path is not None:
        trees.append((path, branch))
    return trees


def worktree_dir(number):
    base = os.getenv("PR_TRIAGE_WORKTREES", "")
    root = Path(base).expanduser() if base else Path(MAIN_CHECKOUT) / ".worktrees"
    return root / f"pr-{number}"


def pr_head_branch(number):
    rc, out, err = sh(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            REPO,
            "--json",
            "headRefName",
            "--jq",
            ".headRefName",
        ]
    )
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip())
    return out.strip()


def list_worktrees():
    if not MAIN_CHECKOUT:
        return []
    rc, out, err = sh(["git", "worktree", "list", "--porcelain"], cwd=MAIN_CHECKOUT)
    if rc != 0:
        raise RuntimeError(err.strip())
    return parse_worktrees(out)


def find_worktree(trees, number, branch):
    """The worktree already holding this PR, as (path, branch), or None.

    Either signal counts: the PR's branch is checked out somewhere, or the
    directory this tool would create is already a worktree (it may hold the
    PR under a different branch name, e.g. one made by `gh pr checkout -b`).
    """
    target = worktree_dir(number) if MAIN_CHECKOUT else None
    for path, existing_branch in trees:
        if (branch and existing_branch == branch) or (target and Path(path) == target):
            return path, existing_branch or branch
    return None


def open_path(command, path):
    """Run `<command> <path>` — the user's editor, file manager, or terminal."""
    argv = shlex.split(command or "")
    if not argv:
        raise RuntimeError("No open command configured")
    exe = shutil.which(argv[0])
    if not exe:
        raise RuntimeError(f"Command not found on PATH: {argv[0]}")
    # No shell: the folder is a separate argv entry, so nothing in a branch or
    # directory name can turn into shell syntax.
    spawn([exe, *argv[1:], str(path)])


def do_checkout(number, open_command=None):
    """Put a PR in a worktree of its own, reusing one when it already exists.

    A worktree leaves whatever you were doing in the main checkout untouched,
    which is the whole point of reviewing from a dashboard: no stashing, and
    several PRs can sit side by side.
    """
    if not MAIN_CHECKOUT:
        raise RuntimeError(
            "No checkout configured — start from inside a repo or set PR_TRIAGE_CHECKOUT"
        )
    branch = pr_head_branch(number)
    existing = find_worktree(list_worktrees(), number, branch)
    if existing:
        path, existing_branch = existing
        result = {"path": path, "branch": existing_branch, "created": False}
        if open_command:
            open_path(open_command, path)
            result["opened"] = open_command
        return result

    target = worktree_dir(number)
    target.parent.mkdir(parents=True, exist_ok=True)
    rc, out, err = sh(["git", "worktree", "add", "--detach", str(target)], cwd=MAIN_CHECKOUT)
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip())
    # Detached first, then let gh name and track the branch — it knows how to
    # reach a fork's head, which a plain `git worktree add <branch>` does not.
    rc, out, err = sh(["gh", "pr", "checkout", str(number), "--repo", REPO], cwd=str(target))
    if rc != 0:
        sh(["git", "worktree", "remove", "--force", str(target)], cwd=MAIN_CHECKOUT)
        raise RuntimeError(err.strip() or out.strip())
    result = {"path": str(target), "branch": branch, "created": True}
    if open_command:
        open_path(open_command, target)
        result["opened"] = open_command
    return result


# Merge states where GitHub would still offer the button. DIRTY means conflicts,
# BLOCKED means a required review or check is missing, DRAFT speaks for itself.
MERGEABLE_STATES = ("CLEAN", "UNSTABLE", "BEHIND", "HAS_HOOKS")
MERGE_FLAGS = {"SQUASH": "--squash", "MERGE": "--merge", "REBASE": "--rebase"}
_merge_method = None


def merge_method():
    """The merge this repo actually allows, so the button can say which it is.

    Repos commonly permit exactly one; asking beats assuming "merge commit".
    """
    global _merge_method
    if _merge_method is None:
        rc, out, err = sh(
            [
                "gh",
                "repo",
                "view",
                REPO,
                "--json",
                "squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed,viewerDefaultMergeMethod",
            ]
        )
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        config = json.loads(out)
        allowed = [
            name
            for name, key in (
                ("SQUASH", "squashMergeAllowed"),
                ("MERGE", "mergeCommitAllowed"),
                ("REBASE", "rebaseMergeAllowed"),
            )
            if config.get(key)
        ]
        default = config.get("viewerDefaultMergeMethod")
        _merge_method = default if default in allowed else (allowed[0] if allowed else None)
    return _merge_method


_commit_config = None


def commit_config():
    """How this repo composes a merge commit, from its settings.

    GitHub builds the message from the PR title and body (or the commits, or
    nothing) depending on four repository settings; the editor should open on
    whatever the button would have produced, not on a guess.
    """
    global _commit_config
    if _commit_config is None:
        rc, out, err = sh(
            [
                "gh",
                "api",
                f"repos/{REPO}",
                "--jq",
                "{squashTitle: .squash_merge_commit_title,"
                " squashMessage: .squash_merge_commit_message,"
                " mergeTitle: .merge_commit_title, mergeMessage: .merge_commit_message}",
            ]
        )
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        _commit_config = json.loads(out)
    return _commit_config


def commit_messages(commits):
    parts = []
    for commit in commits:
        headline = commit.get("messageHeadline", "")
        rest = commit.get("messageBody", "").strip()
        parts.append(f"{headline}\n\n{rest}" if rest else headline)
    return "\n\n".join(parts)


def merge_message(number):
    """The subject and body GitHub would commit, for the editor to open on."""
    method = merge_method()
    if not method:
        raise RuntimeError("This repository allows no merge method")
    if method == "REBASE":
        # Rebasing replays the PR's own commits; there is no message to write.
        return {"method": method, "editable": False, "subject": "", "body": ""}

    rc, out, err = sh(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            REPO,
            "--json",
            "title,body,number,headRefName,commits",
        ]
    )
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip())
    pr = json.loads(out)
    commits = pr.get("commits") or []
    config = commit_config()
    # GitHub trims the title before composing; a PR titled with a trailing
    # space would otherwise give "… v0.5.3  (#1311)".
    pr_title = f"{pr['title'].strip()} (#{pr['number']})"

    if method == "SQUASH":
        if config.get("squashTitle") == "COMMIT_OR_PR_TITLE" and len(commits) == 1:
            subject = commits[0].get("messageHeadline", pr_title)
        else:
            subject = pr_title
        message = config.get("squashMessage") or "PR_BODY"
        body = {"PR_BODY": pr.get("body") or "", "COMMIT_MESSAGES": commit_messages(commits)}.get(
            message, ""
        )
    else:
        subject = (
            pr_title
            if config.get("mergeTitle") == "PR_TITLE"
            else f"Merge pull request #{pr['number']} from {pr['headRefName']}"
        )
        message = config.get("mergeMessage") or "PR_TITLE"
        body = {"PR_BODY": pr.get("body") or "", "PR_TITLE": pr["title"]}.get(message, "")

    return {"method": method, "editable": True, "subject": subject, "body": body}


def do_merge(number, subject=None, body=None):
    method = merge_method()
    if not method:
        raise RuntimeError("This repository allows no merge method")
    args = ["gh", "pr", "merge", str(number), "--repo", REPO, MERGE_FLAGS[method]]
    path = None
    if subject:
        args += ["--subject", subject]
    if body is not None:
        # Through a file, so newlines and backticks reach git untouched.
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write(body)
            path = handle.name
        args += ["--body-file", path]
    try:
        rc, out, err = sh(args)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
    finally:
        if path:
            os.unlink(path)
    return {"number": number, "method": method, "output": (out or err).strip()}


def open_worktree(path, command):
    """Open one of this repo's worktrees.

    The path has to be one git reports rather than free text, so the request
    cannot name an arbitrary directory to hand to the open command.
    """
    known = [(p, b) for p, b in list_worktrees() if p == path]
    if not known:
        raise RuntimeError(f"Not a worktree of this repo: {path}")
    open_path(command, path)
    return {"path": path, "branch": known[0][1], "opened": command}


COMMENTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      title url
      comments(first: 100) {
        nodes { author { login } createdAt url body }
      }
      reviews(first: 100) {
        nodes { author { login } state submittedAt url body }
      }
      reviewThreads(first: 100) {
        nodes {
          isResolved isOutdated path line
          comments(first: 50) { nodes { author { login } createdAt url body } }
        }
      }
    }
  }
}
"""


def trim(body, limit=600):
    body = (body or "").strip()
    return body if len(body) <= limit else body[:limit].rstrip() + "…"


def fetch_comments(number):
    """Everything said on a PR, as threads plus standalone comments.

    Review threads are the tree part: a comment on a line, with its replies.
    Reviews and issue comments have no children but belong in the timeline.
    """
    owner, _, name = REPO.partition("/")
    rc, out, err = sh(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={COMMENTS_QUERY}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-F",
            f"number={number}",
        ]
    )
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip())
    pr = json.loads(out)["data"]["repository"]["pullRequest"]
    if not pr:
        raise RuntimeError(f"No such pull request: #{number}")

    threads = []
    for thread in pr["reviewThreads"]["nodes"]:
        comments = [
            {
                "author": (c.get("author") or {}).get("login", "ghost"),
                "at": c["createdAt"],
                "url": c["url"],
                "body": trim(c["body"]),
            }
            for c in thread["comments"]["nodes"]
        ]
        if not comments:
            continue
        threads.append(
            {
                "kind": "thread",
                "path": thread["path"],
                "line": thread["line"],
                "resolved": thread["isResolved"],
                "outdated": thread["isOutdated"],
                "at": comments[0]["at"],
                "comments": comments,
            }
        )

    singles = [
        {
            "kind": "review",
            "state": r["state"],
            "at": r["submittedAt"],
            "url": r["url"],
            "author": (r.get("author") or {}).get("login", "ghost"),
            "body": trim(r["body"]),
        }
        for r in pr["reviews"]["nodes"]
        # A review with no body is just the approval stamp already on the card.
        if r.get("submittedAt") and (r["body"].strip() or r["state"] != "COMMENTED")
    ] + [
        {
            "kind": "comment",
            "at": c["createdAt"],
            "url": c["url"],
            "author": (c.get("author") or {}).get("login", "ghost"),
            "body": trim(c["body"]),
        }
        for c in pr["comments"]["nodes"]
    ]

    return {
        "number": number,
        "title": pr["title"],
        "url": pr["url"],
        "entries": sorted(threads + singles, key=lambda e: e["at"]),
    }


PR_DIR_RE = re.compile(r"/pr-(\d+)/?$")
GONE = ("CLOSED", "MERGED")


def pr_index():
    """Every PR in the repo, keyed by number and by head branch."""
    rc, out, err = sh(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            REPO,
            "--state",
            "all",
            "--limit",
            "300",
            "--json",
            "number,state,headRefName,title",
        ]
    )
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip())
    by_branch, by_number = {}, {}
    for pr in json.loads(out):
        by_number[pr["number"]] = pr
        # Branches get reused across PRs; the newest one wins, which is the
        # one gh lists first.
        by_branch.setdefault(pr["headRefName"], pr)
    return by_branch, by_number


def pr_for_worktree(path, branch, by_branch, by_number):
    if branch and branch in by_branch:
        return by_branch[branch]
    found = PR_DIR_RE.search(path)
    return by_number.get(int(found.group(1))) if found else None


def is_dirty(path):
    """True if `git worktree remove` would refuse — modified *or* untracked."""
    rc, out, _ = sh(["git", "status", "--porcelain"], cwd=path)
    return rc != 0 or bool(out.strip())


def local_branches():
    rc, out, err = sh(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"], cwd=MAIN_CHECKOUT
    )
    if rc != 0:
        raise RuntimeError(err.strip())
    return [line for line in out.splitlines() if line.strip()]


def cleanup_candidates():
    """Worktrees and branches whose PR is closed or merged.

    The main checkout is never a candidate, and a dirty worktree is listed but
    flagged, because removing it would throw away work that is not on a remote.
    """
    if not MAIN_CHECKOUT:
        raise RuntimeError("No checkout configured")
    trees = list_worktrees()
    by_branch, by_number = pr_index()
    live = {branch for _, branch in trees if branch}
    items = []

    for path, branch in trees:
        if path == MAIN_CHECKOUT:
            continue
        pr = pr_for_worktree(path, branch, by_branch, by_number)
        if not pr or pr["state"] not in GONE:
            continue
        items.append(
            {
                "kind": "worktree",
                "name": path,
                "branch": branch,
                "number": pr["number"],
                "state": pr["state"],
                "title": pr["title"],
                "dirty": is_dirty(path),
            }
        )

    for branch in local_branches():
        pr = by_branch.get(branch)
        if not pr or pr["state"] not in GONE:
            continue
        items.append(
            {
                "kind": "branch",
                "name": branch,
                "branch": branch,
                "number": pr["number"],
                "state": pr["state"],
                "title": pr["title"],
                # Its worktree has to go first; git will not delete a checked-out branch.
                "inWorktree": branch in live,
            }
        )
    return {"items": items, "mainCheckout": MAIN_CHECKOUT}


def do_cleanup(paths, branches):
    """Remove the named worktrees, then delete the named branches.

    Every name is checked against a freshly computed candidate list, so a
    request cannot remove a worktree or branch that this tool would not offer.
    """
    items = cleanup_candidates()["items"]
    removable = {i["name"] for i in items if i["kind"] == "worktree" and not i["dirty"]}
    deletable = {i["name"] for i in items if i["kind"] == "branch"}
    results = []

    def run(kind, name, allowed, args):
        if name not in allowed:
            results.append({"kind": kind, "name": name, "error": f"not a {kind} of a closed PR"})
            return
        rc, out, err = sh(args, cwd=MAIN_CHECKOUT)
        results.append(
            {"kind": kind, "name": name} | ({"error": (err or out).strip()} if rc else {"ok": True})
        )

    # Worktrees first: a branch cannot be deleted while one has it checked out.
    for path in paths:
        run("worktree", path, removable, ["git", "worktree", "remove", path])
    for branch in branches:
        run("branch", branch, deletable, ["git", "branch", "-D", branch])
    return {"results": results}


def do_edit(number, add_flag, remove_flag, add, remove, validator, what):
    for name in add + remove:
        if not validator.match(name):
            raise RuntimeError(f"Invalid {what}: {name!r}")
    args = ["gh", "pr", "edit", str(number), "--repo", REPO]
    if add:
        args += [add_flag, ",".join(add)]
    if remove:
        args += [remove_flag, ",".join(remove)]
    rc, out, err = sh(args)
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip())
    return {"ok": True}


def list_labels():
    rc, out, err = sh(
        ["gh", "label", "list", "--repo", REPO, "--json", "name,color", "--limit", "200"]
    )
    if rc != 0:
        raise RuntimeError(err.strip())
    return json.loads(out)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout quiet; errors surface via HTTP responses

    def send_json(self, status, obj):
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def send_static(self, name, content_type):
        body = (HERE / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path in STATIC:
                self.send_static(*STATIC[url.path])
            elif url.path == "/api/prs":
                q = parse_qs(url.query).get("q", [DEFAULT_QUERY])[0]
                self.send_json(200, fetch_prs(q))
            elif url.path == "/api/labels":
                self.send_json(200, list_labels())
            elif url.path == "/api/worktrees":
                self.send_json(
                    200,
                    {
                        "mainCheckout": MAIN_CHECKOUT,
                        "worktrees": [{"path": p, "branch": b} for p, b in list_worktrees()],
                    },
                )
            elif url.path == "/api/cleanup":
                self.send_json(200, cleanup_candidates())
            elif url.path == "/api/comments":
                self.send_json(
                    200, fetch_comments(int(parse_qs(url.query).get("number", ["0"])[0]))
                )
            elif url.path == "/api/tasks":
                self.send_json(200, fetch_tasks(int(parse_qs(url.query).get("number", ["0"])[0])))
            elif url.path == "/api/merge-message":
                self.send_json(200, merge_message(int(parse_qs(url.query).get("number", ["0"])[0])))
            else:
                self.send_json(404, {"error": "not found"})
        except Exception as e:  # surface any gh/parse failure to the UI banner
            self.send_json(500, {"error": str(e)})

    def guard(self):
        """Refuse POSTs that another site in the browser could have sent.

        These endpoints run git, gh, and the configured open command, so a page
        on any origin must not be able to reach them. Requiring a header that
        is not CORS-safelisted forces a preflight, which this server never
        answers, and the Origin check covers the simple requests that skip one.
        """
        origin = self.headers.get("Origin")
        if origin and origin not in ALLOWED_ORIGINS:
            raise PermissionError(f"cross-origin request from {origin}")
        if self.headers.get("X-PR-Triage") != "1":
            raise PermissionError("missing X-PR-Triage header")

    def do_POST(self):
        url = urlparse(self.path)
        try:
            self.guard()
            body = self.read_body()
            if url.path == "/api/open":
                self.send_json(200, open_worktree(body["path"], body.get("open")))
                return
            if url.path == "/api/cleanup":
                self.send_json(200, do_cleanup(body.get("worktrees", []), body.get("branches", [])))
                return
            number = int(body["number"])
            if url.path == "/api/tasks":
                self.send_json(200, set_task(number, body["index"], body["done"]))
                return
            if url.path == "/api/merge":
                self.send_json(200, do_merge(number, body.get("subject"), body.get("body")))
                return
            if url.path == "/api/checkout":
                self.send_json(200, do_checkout(number, body.get("open")))
            elif url.path == "/api/labels":
                self.send_json(
                    200,
                    do_edit(
                        number,
                        "--add-label",
                        "--remove-label",
                        body.get("add", []),
                        body.get("remove", []),
                        LABEL_RE,
                        "label",
                    ),
                )
            elif url.path == "/api/assignees":
                self.send_json(
                    200,
                    do_edit(
                        number,
                        "--add-assignee",
                        "--remove-assignee",
                        body.get("add", []),
                        body.get("remove", []),
                        LOGIN_RE,
                        "assignee",
                    ),
                )
            else:
                self.send_json(404, {"error": "not found"})
        except PermissionError as e:
            self.send_json(403, {"error": str(e)})
        except (KeyError, ValueError) as e:
            self.send_json(400, {"error": f"bad request: {e}"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})


def main():
    global REPO, MAIN_CHECKOUT
    REPO = detect_repo()
    MAIN_CHECKOUT = detect_main_checkout()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"PR triage for {REPO} → http://127.0.0.1:{PORT}")
    print(f"Checkout button operates on: {MAIN_CHECKOUT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
