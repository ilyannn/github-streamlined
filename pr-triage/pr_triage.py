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
    PR_TRIAGE_QUERY     default search query        (default: is:open)
"""

import json
import os
import re
import subprocess
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PORT = int(os.getenv("PR_TRIAGE_PORT", "8642"))
DEFAULT_QUERY = os.getenv("PR_TRIAGE_QUERY", "is:open")

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

    prs = []
    for pr in data["search"]["nodes"]:
        if not pr:  # non-PR search results come back as empty objects
            continue
        bucket, badges = classify(pr, me)
        done, total = count_tasks(pr.get("body"))
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


def do_checkout(number):
    if not MAIN_CHECKOUT:
        raise RuntimeError(
            "No checkout configured — start from inside a repo or set PR_TRIAGE_CHECKOUT"
        )
    rc, out, err = sh(["git", "status", "--porcelain", "--untracked-files=no"], cwd=MAIN_CHECKOUT)
    if rc != 0:
        raise RuntimeError(err.strip())
    if out.strip():
        raise RuntimeError(f"{MAIN_CHECKOUT} has uncommitted changes — commit or stash them first")
    rc, out, err = sh(["gh", "pr", "checkout", str(number)], cwd=MAIN_CHECKOUT)
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip())
    rc, branch, _ = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=MAIN_CHECKOUT)
    return {"path": MAIN_CHECKOUT, "branch": branch.strip()}


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

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path in ("/", "/index.html"):
                html = (HERE / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            elif url.path == "/api/prs":
                q = parse_qs(url.query).get("q", [DEFAULT_QUERY])[0]
                self.send_json(200, fetch_prs(q))
            elif url.path == "/api/labels":
                self.send_json(200, list_labels())
            else:
                self.send_json(404, {"error": "not found"})
        except Exception as e:  # surface any gh/parse failure to the UI banner
            self.send_json(500, {"error": str(e)})

    def do_POST(self):
        url = urlparse(self.path)
        try:
            body = self.read_body()
            number = int(body["number"])
            if url.path == "/api/checkout":
                self.send_json(200, do_checkout(number))
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
