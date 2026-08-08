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
        headRefName additions deletions
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
        commits(last: 1) { nodes { commit { committedDate statusCheckRollup { state } } } }
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


def count_tasks(body):
    if not body:
        return 0, 0
    boxes = re.findall(r"^\s*[-*]\s+\[( |x|X)\]", body, re.MULTILINE)
    return sum(1 for b in boxes if b in "xX"), len(boxes)


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

    if draft:
        bucket = "drafts"
    elif mine:
        if decision == "CHANGES_REQUESTED" or ci in ("FAILURE", "ERROR") or others_new:
            bucket = "yours_act"
        elif decision == "APPROVED" and ci_ok:
            bucket = "merge_ready"
        else:
            bucket = "waiting"
    else:
        needs_me = (
            requested_of_me
            or pushed_since_review
            or others_new
            or (not my_last and decision in (None, "REVIEW_REQUIRED"))
        )
        if decision == "APPROVED" and ci_ok:
            bucket = "merge_ready"
        elif needs_me:
            bucket = "review"
        else:
            bucket = "waiting"

    return bucket, badges


def fetch_prs(user_query):
    search = f"repo:{REPO} is:pr {user_query}"
    rc, out, err = sh(["gh", "api", "graphql", "-f", f"query={GRAPHQL_QUERY}", "-f", f"q={search}"])
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip())
    data = json.loads(out)["data"]
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
    argv = shlex.split(command)
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
            number = int(body["number"])
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
