"""Unit tests for the pure logic: classification, parsing, gh command building."""

import json

import pr_triage
import pytest
from pr_triage import LABEL_RE, LOGIN_RE, classify, count_tasks, do_edit, ts

ME = "me"


def t(hour):
    return f"2026-08-08T{hour:02d}:00:00Z"


def make_pr(
    author="alice",
    draft=False,
    decision=None,
    ci=None,
    commit_at=None,
    reviews=(),
    comments=(),
    requested=(),
):
    """Build a PR dict in the GraphQL shape classify() consumes.

    reviews:  (login, state, submitted_at) tuples
    comments: (login, created_at) tuples
    """
    commit_at = commit_at or t(0)
    return {
        "isDraft": draft,
        "reviewDecision": decision,
        "author": {"login": author, "avatarUrl": ""} if author else None,
        "commits": {
            "totalCount": 3,
            "nodes": [
                {
                    "commit": {
                        "committedDate": commit_at,
                        "statusCheckRollup": {"state": ci} if ci else None,
                    }
                }
            ],
        },
        "reviews": {
            "nodes": [
                {"author": {"login": login}, "state": state, "submittedAt": at}
                for login, state, at in reviews
            ]
        },
        "comments": {
            "totalCount": len(comments),
            "nodes": [{"author": {"login": login}, "createdAt": at} for login, at in comments],
        },
        "reviewRequests": {
            "nodes": [{"requestedReviewer": {"login": login}} for login in requested]
        },
    }


def badge_texts(badges):
    return [b["text"] for b in badges]


class TestClassify:
    def test_draft_wins_over_everything(self):
        pr = make_pr(author=ME, draft=True, decision="CHANGES_REQUESTED", ci="FAILURE")
        bucket, _ = classify(pr, ME)
        assert bucket == "drafts"

    def test_mine_changes_requested(self):
        bucket, badges = classify(make_pr(author=ME, decision="CHANGES_REQUESTED"), ME)
        assert bucket == "yours_act"
        assert "changes requested" in badge_texts(badges)

    def test_mine_failing_ci(self):
        bucket, badges = classify(make_pr(author=ME, ci="FAILURE"), ME)
        assert bucket == "yours_act"
        assert "CI failing" in badge_texts(badges)

    def test_mine_approved_green_is_merge_ready(self):
        bucket, badges = classify(make_pr(author=ME, decision="APPROVED", ci="SUCCESS"), ME)
        assert bucket == "merge_ready"
        assert "approved" in badge_texts(badges)

    def test_mine_new_comments_need_action(self):
        pr = make_pr(author=ME, comments=[(ME, t(1)), ("alice", t(2))])
        bucket, badges = classify(pr, ME)
        assert bucket == "yours_act"
        assert "1 new comment" in badge_texts(badges)

    def test_mine_quiet_is_waiting(self):
        bucket, _ = classify(make_pr(author=ME), ME)
        assert bucket == "waiting"

    def test_pushed_since_review_badge_suppressed_on_own_pr(self):
        # Inline comments on your own PR create COMMENTED pseudo-reviews;
        # a later push must not claim "pushed since your review".
        pr = make_pr(author=ME, reviews=[(ME, "COMMENTED", t(1))], commit_at=t(2))
        _, badges = classify(pr, ME)
        assert "pushed since your review" not in badge_texts(badges)

    def test_other_unseen_needs_review(self):
        bucket, badges = classify(make_pr(decision="REVIEW_REQUIRED"), ME)
        assert bucket == "review"
        assert "not seen by you" in badge_texts(badges)

    def test_other_review_requested_of_me(self):
        bucket, badges = classify(make_pr(requested=[ME]), ME)
        assert bucket == "review"
        assert "your review requested" in badge_texts(badges)

    def test_other_pushed_since_my_review(self):
        pr = make_pr(
            decision="REVIEW_REQUIRED",
            reviews=[(ME, "APPROVED", t(1))],
            commit_at=t(2),
        )
        bucket, badges = classify(pr, ME)
        assert bucket == "review"
        assert "pushed since your review" in badge_texts(badges)

    def test_other_new_review_after_my_comment(self):
        pr = make_pr(
            comments=[(ME, t(1))],
            reviews=[("alice", "COMMENTED", t(2))],
            commit_at=t(0),
        )
        bucket, badges = classify(pr, ME)
        assert bucket == "review"
        assert "1 new comment" in badge_texts(badges)

    def test_other_changes_requested_waits_on_author(self):
        pr = make_pr(
            decision="CHANGES_REQUESTED",
            reviews=[(ME, "CHANGES_REQUESTED", t(2))],
            commit_at=t(1),
        )
        bucket, _ = classify(pr, ME)
        assert bucket == "waiting"

    def test_other_approved_green_is_merge_ready(self):
        bucket, _ = classify(make_pr(decision="APPROVED", ci="SUCCESS"), ME)
        assert bucket == "merge_ready"

    def test_other_approved_red_is_not_merge_ready(self):
        bucket, badges = classify(make_pr(decision="APPROVED", ci="FAILURE"), ME)
        assert bucket != "merge_ready"
        assert "CI failing" in badge_texts(badges)

    def test_deleted_author_is_not_mine(self):
        bucket, _ = classify(make_pr(author=None), ME)
        assert bucket == "review"


class TestParsing:
    def test_ts(self):
        assert ts(None) is None
        parsed = ts("2026-08-08T12:00:00Z")
        assert parsed.tzinfo is not None
        assert ts("2026-08-08T13:00:00Z") > parsed

    def test_count_tasks(self):
        assert count_tasks(None) == (0, 0)
        assert count_tasks("no tasks here") == (0, 0)
        body = "- [x] one\n- [ ] two\n* [X] three\n  - [ ] indented\ntext - [ ] not a bullet"
        assert count_tasks(body) == (2, 4)


class TestDoEdit:
    def test_rejects_label_with_comma(self, monkeypatch):
        monkeypatch.setattr(pr_triage, "sh", lambda *a, **k: pytest.fail("sh called"))
        with pytest.raises(RuntimeError, match="Invalid label"):
            do_edit(1, "--add-label", "--remove-label", ["a,b"], [], LABEL_RE, "label")

    def test_rejects_bad_login(self, monkeypatch):
        monkeypatch.setattr(pr_triage, "sh", lambda *a, **k: pytest.fail("sh called"))
        with pytest.raises(RuntimeError, match="Invalid assignee"):
            do_edit(
                1, "--add-assignee", "--remove-assignee", ["evil; rm -rf"], [], LOGIN_RE, "assignee"
            )

    def test_builds_gh_command(self, monkeypatch):
        calls = []
        monkeypatch.setattr(pr_triage, "sh", lambda args, **k: (calls.append(args), (0, "", ""))[1])
        monkeypatch.setattr(pr_triage, "REPO", "acme/widgets")
        do_edit(7, "--add-label", "--remove-label", ["a b"], ["c"], LABEL_RE, "label")
        assert calls == [
            [
                "gh",
                "pr",
                "edit",
                "7",
                "--repo",
                "acme/widgets",
                "--add-label",
                "a b",
                "--remove-label",
                "c",
            ]
        ]


class TestFetchPrs:
    def test_parses_graphql_response(self, monkeypatch):
        pr = make_pr(decision="REVIEW_REQUIRED") | {
            "number": 42,
            "title": "Add frobnicator",
            "url": "https://example.invalid/pr/42",
            "body": "- [x] done\n- [ ] pending",
            "createdAt": t(0),
            "updatedAt": t(1),
            "headRefName": "feature/frob",
            "additions": 10,
            "deletions": 2,
            "labels": {"nodes": [{"name": "bug", "color": "ff0000"}]},
            "assignees": {"nodes": [{"login": "alice"}]},
        }
        payload = {
            "data": {
                "viewer": {"login": ME, "avatarUrl": ""},
                "search": {"issueCount": 1, "nodes": [pr, {}]},  # {} = non-PR result
            }
        }
        calls = []
        monkeypatch.setattr(
            pr_triage, "sh", lambda args, **k: (calls.append(args), (0, json.dumps(payload), ""))[1]
        )
        monkeypatch.setattr(pr_triage, "REPO", "acme/widgets")

        out = pr_triage.fetch_prs("is:open")

        assert calls[0][-1] == "q=repo:acme/widgets is:pr is:open"
        assert out["query"] == "is:open"
        [got] = out["prs"]
        assert got["number"] == 42
        assert got["bucket"] == "review"
        assert (got["tasksDone"], got["tasksTotal"]) == (1, 2)
        assert got["assignees"] == ["alice"]
        assert got["labels"] == [{"name": "bug", "color": "ff0000"}]
        assert got["commitCount"] == 3
        assert got["worktree"] is None

    def test_merge_readiness_travels_with_each_pr(self, monkeypatch):
        def payload(mergeable, state):
            pr = make_pr() | {
                "number": 1,
                "title": "t",
                "url": "u",
                "body": "",
                "createdAt": t(0),
                "updatedAt": t(1),
                "headRefName": "b",
                "additions": 1,
                "deletions": 0,
                "labels": {"nodes": []},
                "assignees": {"nodes": []},
                "mergeable": mergeable,
                "mergeStateStatus": state,
            }
            return {
                "data": {
                    "viewer": {"login": ME, "avatarUrl": ""},
                    "search": {"issueCount": 1, "nodes": [pr]},
                }
            }

        def run(mergeable, state):
            monkeypatch.setattr(
                pr_triage, "sh", lambda args, **k: (0, json.dumps(payload(mergeable, state)), "")
            )
            monkeypatch.setattr(pr_triage, "merge_method", lambda: "SQUASH")
            return pr_triage.fetch_prs("is:open")["prs"][0]["canMerge"]

        assert run("MERGEABLE", "CLEAN") is True
        assert run("MERGEABLE", "BEHIND") is True
        assert run("MERGEABLE", "BLOCKED") is False  # a required check or review is missing
        assert run("CONFLICTING", "DIRTY") is False
        assert run("UNKNOWN", "UNKNOWN") is False  # GitHub has not worked it out yet
