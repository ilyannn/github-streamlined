"""Cleanup candidates and the guarded removal that follows."""

import json
from pathlib import Path

import pr_triage
import pytest
from pr_triage import cleanup_candidates, do_cleanup

PRS = [
    {"number": 10, "state": "MERGED", "headRefName": "feat/merged", "title": "Merged work"},
    {"number": 11, "state": "CLOSED", "headRefName": "feat/closed", "title": "Abandoned"},
    {"number": 12, "state": "OPEN", "headRefName": "feat/open", "title": "Still going"},
]

PORCELAIN = """\
worktree /repo
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree /repo/.worktrees/pr-10
HEAD 2222222222222222222222222222222222222222
branch refs/heads/feat/merged

worktree /repo/.worktrees/pr-12
HEAD 3333333333333333333333333333333333333333
branch refs/heads/feat/open

worktree /repo/.worktrees/pr-11
HEAD 4444444444444444444444444444444444444444
branch refs/heads/feat/closed
"""

BRANCHES = "main\nfeat/merged\nfeat/closed\nfeat/open\nfeat/no-pr\n"


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.setattr(pr_triage, "MAIN_CHECKOUT", "/repo")
    monkeypatch.setattr(pr_triage, "REPO", "acme/widgets")
    return "/repo"


def wire(monkeypatch, dirty=(), removals=None):
    calls = []

    def fake_sh(args, cwd=None, **kwargs):
        calls.append(args)
        if args[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps(PRS), ""
        if args[:3] == ["git", "worktree", "list"]:
            return 0, PORCELAIN, ""
        if args[:2] == ["git", "for-each-ref"]:
            return 0, BRANCHES, ""
        if args[:2] == ["git", "status"]:
            return (0, "M f.py\n", "") if cwd in dirty else (0, "", "")
        if args[:3] == ["git", "worktree", "remove"] or args[:2] == ["git", "branch"]:
            return removals.get(args[-1], (0, "", "")) if removals else (0, "", "")
        raise AssertionError(f"unexpected: {args}")

    monkeypatch.setattr(pr_triage, "sh", fake_sh)
    return calls


class TestCandidates:
    def test_offers_only_closed_and_merged(self, repo, monkeypatch):
        wire(monkeypatch)
        items = cleanup_candidates()["items"]
        worktrees = [i["name"] for i in items if i["kind"] == "worktree"]
        branches = [i["name"] for i in items if i["kind"] == "branch"]
        assert worktrees == ["/repo/.worktrees/pr-10", "/repo/.worktrees/pr-11"]
        assert branches == ["feat/merged", "feat/closed"]

    def test_never_offers_the_main_checkout(self, repo, monkeypatch):
        wire(monkeypatch)
        assert all(i["name"] != "/repo" for i in cleanup_candidates()["items"])

    def test_ignores_a_branch_with_no_pr(self, repo, monkeypatch):
        wire(monkeypatch)
        assert all(i["name"] != "feat/no-pr" for i in cleanup_candidates()["items"])

    def test_flags_a_dirty_worktree(self, repo, monkeypatch):
        wire(monkeypatch, dirty={"/repo/.worktrees/pr-10"})
        by_name = {i["name"]: i for i in cleanup_candidates()["items"]}
        assert by_name["/repo/.worktrees/pr-10"]["dirty"] is True
        assert by_name["/repo/.worktrees/pr-11"]["dirty"] is False

    def test_marks_branches_still_checked_out(self, repo, monkeypatch):
        wire(monkeypatch)
        branches = {i["name"]: i for i in cleanup_candidates()["items"] if i["kind"] == "branch"}
        assert branches["feat/merged"]["inWorktree"] is True


class TestDoCleanup:
    def test_removes_worktrees_before_branches(self, repo, monkeypatch):
        calls = wire(monkeypatch)
        do_cleanup(["/repo/.worktrees/pr-10"], ["feat/merged"])
        actions = [
            c for c in calls if c[:3] == ["git", "worktree", "remove"] or c[:2] == ["git", "branch"]
        ]
        # Deleting the branch first would fail while its worktree still exists.
        assert actions == [
            ["git", "worktree", "remove", "/repo/.worktrees/pr-10"],
            ["git", "branch", "-D", "feat/merged"],
        ]

    def test_refuses_a_worktree_of_an_open_pr(self, repo, monkeypatch):
        calls = wire(monkeypatch)
        result = do_cleanup(["/repo/.worktrees/pr-12"], [])
        assert "not a worktree of a closed PR" in result["results"][0]["error"]
        assert not [c for c in calls if c[:3] == ["git", "worktree", "remove"]]

    def test_refuses_an_arbitrary_path(self, repo, monkeypatch):
        calls = wire(monkeypatch)
        result = do_cleanup(["/etc"], ["main"])
        assert [r["error"] for r in result["results"]] == [
            "not a worktree of a closed PR",
            "not a branch of a closed PR",
        ]
        assert not [c for c in calls if c[:3] == ["git", "worktree", "remove"]]
        assert not [c for c in calls if c[:2] == ["git", "branch"]]

    def test_refuses_a_dirty_worktree(self, repo, monkeypatch):
        wire(monkeypatch, dirty={"/repo/.worktrees/pr-10"})
        result = do_cleanup(["/repo/.worktrees/pr-10"], [])
        assert "error" in result["results"][0]

    def test_reports_a_failure_per_item(self, repo, monkeypatch):
        wire(monkeypatch, removals={"feat/closed": (1, "", "not fully merged")})
        result = do_cleanup([], ["feat/merged", "feat/closed"])
        assert result["results"][0] == {"kind": "branch", "name": "feat/merged", "ok": True}
        assert result["results"][1]["error"] == "not fully merged"


class TestMergeMethod:
    def test_uses_the_only_method_the_repo_allows(self, repo, monkeypatch):
        monkeypatch.setattr(pr_triage, "_merge_method", None)
        monkeypatch.setattr(
            pr_triage,
            "sh",
            lambda args, **k: (
                0,
                json.dumps(
                    {
                        "squashMergeAllowed": True,
                        "mergeCommitAllowed": False,
                        "rebaseMergeAllowed": False,
                        "viewerDefaultMergeMethod": "MERGE",
                    }
                ),
                "",
            ),
        )
        # The viewer default is not allowed here, so it must not win.
        assert pr_triage.merge_method() == "SQUASH"

    def test_prefers_the_viewer_default_when_allowed(self, repo, monkeypatch):
        monkeypatch.setattr(pr_triage, "_merge_method", None)
        monkeypatch.setattr(
            pr_triage,
            "sh",
            lambda args, **k: (
                0,
                json.dumps(
                    {
                        "squashMergeAllowed": True,
                        "mergeCommitAllowed": True,
                        "rebaseMergeAllowed": False,
                        "viewerDefaultMergeMethod": "MERGE",
                    }
                ),
                "",
            ),
        )
        assert pr_triage.merge_method() == "MERGE"

    def test_merge_sends_an_edited_message_through_a_file(self, repo, monkeypatch):
        monkeypatch.setattr(pr_triage, "_merge_method", "SQUASH")
        seen = {}

        def fake_sh(args, **kwargs):
            seen["args"] = args
            seen["body"] = Path(args[args.index("--body-file") + 1]).read_text()
            return 0, "merged", ""

        monkeypatch.setattr(pr_triage, "sh", fake_sh)
        pr_triage.do_merge(7, "Custom subject (#7)", "Line one\n\nLine two")

        assert "--subject" in seen["args"]
        assert seen["args"][seen["args"].index("--subject") + 1] == "Custom subject (#7)"
        assert seen["body"] == "Line one\n\nLine two"
        # The temp file must not outlive the call.
        assert not Path(seen["args"][seen["args"].index("--body-file") + 1]).exists()

    def test_merge_without_edits_lets_github_compose(self, repo, monkeypatch):
        monkeypatch.setattr(pr_triage, "_merge_method", "SQUASH")
        calls = []
        monkeypatch.setattr(
            pr_triage, "sh", lambda args, **k: (calls.append(args), (0, "merged", ""))[1]
        )
        pr_triage.do_merge(7)
        assert "--subject" not in calls[0]
        assert "--body-file" not in calls[0]

    def test_merge_passes_the_matching_flag(self, repo, monkeypatch):
        monkeypatch.setattr(pr_triage, "_merge_method", "SQUASH")
        calls = []
        monkeypatch.setattr(
            pr_triage, "sh", lambda args, **k: (calls.append(args), (0, "merged", ""))[1]
        )
        assert pr_triage.do_merge(7)["method"] == "SQUASH"
        assert calls == [["gh", "pr", "merge", "7", "--repo", "acme/widgets", "--squash"]]
