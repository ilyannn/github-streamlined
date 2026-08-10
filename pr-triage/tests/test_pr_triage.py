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

    def test_mine_approved_green_is_yours_to_merge(self):
        # Merge-ready collects other people's landable work; landing your own
        # is just the next thing on your list.
        bucket, badges = classify(make_pr(author=ME, decision="APPROVED", ci="SUCCESS"), ME)
        assert bucket == "yours_act"
        assert "approved" in badge_texts(badges)

    def test_mine_new_comments_need_action(self):
        pr = make_pr(author=ME, comments=[(ME, t(1)), ("alice", t(2))])
        bucket, badges = classify(pr, ME)
        assert bucket == "yours_act"
        assert "1 new comment" in badge_texts(badges)

    def test_mine_quiet_is_waiting(self):
        # Nobody has reviewed it yet, so it really is waiting on other people.
        bucket, _ = classify(make_pr(author=ME), ME)
        assert bucket == "waiting"

    def test_mine_approved_with_ci_running_is_yours_to_act_on(self):
        # Reviewers are finished; the only thing left is CI and then merging,
        # which is your move, not theirs.
        pr = make_pr(author=ME, decision="APPROVED", ci="PENDING")
        bucket, badges = classify(pr, ME)
        assert bucket == "yours_act"
        assert "CI running" in badge_texts(badges)

    def test_someone_elses_approved_pr_with_ci_running_is_not_mine_to_act_on(self):
        pr = make_pr(decision="APPROVED", ci="PENDING", reviews=[(ME, "APPROVED", t(2))])
        bucket, _ = classify(pr, ME)
        assert bucket == "waiting"

    def test_no_pr_of_yours_lands_in_merge_ready(self):
        # Whatever state it is in, your own PR is never filed under other
        # people's landable work.
        for extra in (
            {"decision": "APPROVED", "ci": "SUCCESS"},
            {"decision": "APPROVED", "ci": "PENDING"},
            {"decision": "APPROVED", "ci": "SUCCESS", "comments": [(ME, t(1)), ("alice", t(2))]},
            {"decision": "CHANGES_REQUESTED"},
            {"ci": "FAILURE"},
            {},
        ):
            bucket, _ = classify(make_pr(author=ME, **extra), ME)
            assert bucket != "merge_ready", extra

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

    def test_conflicts_are_not_merge_ready(self):
        # Approved and green, but nobody can land it until it is rebased, so it
        # is work for its author rather than a merge candidate.
        pr = make_pr(author=ME, decision="APPROVED", ci="SUCCESS") | {"mergeable": "CONFLICTING"}
        bucket, badges = classify(pr, ME)
        assert bucket == "yours_act"
        assert "conflicts" in badge_texts(badges)

    def test_someone_elses_conflicted_pr_waits_on_them(self):
        pr = make_pr(decision="APPROVED", ci="SUCCESS", reviews=[(ME, "APPROVED", t(2))]) | {
            "mergeable": "CONFLICTING"
        }
        bucket, badges = classify(pr, ME)
        assert bucket == "waiting"
        assert "conflicts" in badge_texts(badges)

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


class TestMergeBlockers:
    """Every reason, ordered by what to deal with first."""

    def blockers(self, decision=None, ci=None, mergeable="MERGEABLE", state="CLEAN", draft=False):
        pr = make_pr(draft=draft) | {"mergeable": mergeable, "mergeStateStatus": state}
        return pr_triage.merge_blockers(pr, decision, ci)

    def test_nothing_blocks_an_approved_clean_pr(self):
        assert self.blockers(decision="APPROVED", ci="SUCCESS") == []

    def test_draft_says_only_that(self):
        assert self.blockers(draft=True, mergeable="CONFLICTING") == ["draft"]

    def test_a_stale_unapproved_pr_reports_both(self):
        # The review is the gate, the stale branch the last mile. Reporting only
        # "behind base" would send you to update a branch that still cannot
        # merge afterwards.
        assert self.blockers(decision="REVIEW_REQUIRED", state="BEHIND") == [
            "needs approval",
            "behind base",
        ]

    def test_review_comes_before_the_branch(self):
        assert self.blockers(decision="CHANGES_REQUESTED", state="BEHIND") == [
            "changes requested",
            "behind base",
        ]

    def test_checks_come_before_review(self):
        assert self.blockers(decision="REVIEW_REQUIRED", ci="FAILURE") == [
            "checks failing",
            "needs approval",
        ]

    def test_conflicts_come_first_of_all(self):
        assert self.blockers(mergeable="CONFLICTING", ci="FAILURE", decision="REVIEW_REQUIRED") == [
            "conflicts with base",
            "checks failing",
            "needs approval",
        ]

    def test_running_checks_are_a_blocker_too(self):
        assert self.blockers(decision="APPROVED", ci="PENDING") == ["checks running"]

    def test_approved_and_green_but_only_stale(self):
        assert self.blockers(decision="APPROVED", ci="SUCCESS", state="BEHIND") == ["behind base"]

    def test_branch_rules_when_nothing_else_explains_it(self):
        assert self.blockers(decision="APPROVED", ci="SUCCESS", state="BLOCKED") == [
            "blocked by branch rules"
        ]

    def test_unknown_while_github_computes(self):
        assert self.blockers(
            decision="APPROVED", ci="SUCCESS", mergeable="UNKNOWN", state="UNKNOWN"
        ) == ["GitHub is still working it out"]


class TestUnresolvedThreads:
    def test_counts_only_the_open_ones(self):
        pr = make_pr() | {
            "reviewThreads": {
                "nodes": [{"isResolved": True}, {"isResolved": False}, {"isResolved": False}]
            }
        }
        assert pr_triage.unresolved_threads(pr) == 2

    def test_none_when_absent(self):
        assert pr_triage.unresolved_threads(make_pr()) == 0
        assert pr_triage.unresolved_threads(make_pr() | {"reviewThreads": {"nodes": []}}) == 0


class TestAutoMerge:
    def test_absent_when_not_armed(self):
        assert pr_triage.auto_merge(make_pr()) is None
        assert pr_triage.auto_merge(make_pr() | {"autoMergeRequest": None}) is None

    def test_reports_who_armed_it_and_how(self):
        pr = make_pr() | {
            "autoMergeRequest": {
                "enabledAt": t(3),
                "mergeMethod": "SQUASH",
                "enabledBy": {"login": "octocat"},
            }
        }
        assert pr_triage.auto_merge(pr) == {"method": "squash", "at": t(3), "by": "octocat"}

    def test_copes_with_a_deleted_enabler(self):
        pr = make_pr() | {
            "autoMergeRequest": {"enabledAt": t(3), "mergeMethod": "MERGE", "enabledBy": None}
        }
        assert pr_triage.auto_merge(pr) == {"method": "merge", "at": t(3), "by": None}


class TestMergeabilityRetry:
    """GitHub answers UNKNOWN the first time and computes in the background."""

    def cold_then_warm(self, monkeypatch, decision, draft=False):
        pr = make_pr(author="alice", decision=decision, draft=draft)
        replies = [
            {"search": {"nodes": [pr | {"mergeable": "UNKNOWN"}]}},
            {"search": {"nodes": [pr | {"mergeable": "MERGEABLE"}]}},
        ]
        monkeypatch.setattr(
            pr_triage, "time", type("T", (), {"sleep": staticmethod(lambda s: None)})
        )
        return replies

    def test_asks_again_when_an_approved_pr_is_unknown(self, monkeypatch):
        replies = self.cold_then_warm(monkeypatch, "APPROVED")
        assert pr_triage.waiting_on_mergeability(replies[0]) is True
        assert pr_triage.waiting_on_mergeability(replies[1]) is False

    def test_does_not_wait_on_prs_that_could_not_merge_anyway(self, monkeypatch):
        # No point paying for a second round trip for something unapprovable.
        for decision in ("CHANGES_REQUESTED", "REVIEW_REQUIRED", None):
            replies = self.cold_then_warm(monkeypatch, decision)
            assert pr_triage.waiting_on_mergeability(replies[0]) is False

    def test_ignores_drafts(self, monkeypatch):
        replies = self.cold_then_warm(monkeypatch, "APPROVED", draft=True)
        assert pr_triage.waiting_on_mergeability(replies[0]) is False

    def test_retries_once_and_uses_the_second_answer(self, monkeypatch):
        calls = []

        def fake_run(search):
            calls.append(search)
            state = "UNKNOWN" if len(calls) == 1 else "MERGEABLE"
            pr = make_pr(author="alice", decision="APPROVED") | {
                "number": 1,
                "title": "t",
                "url": "u",
                "body": "",
                "createdAt": t(0),
                "updatedAt": t(1),
                "headRefName": "b",
                "additions": 0,
                "deletions": 0,
                "labels": {"nodes": []},
                "assignees": {"nodes": []},
                "mergeable": state,
                "mergeStateStatus": "CLEAN" if state == "MERGEABLE" else "UNKNOWN",
            }
            return {
                "viewer": {"login": ME, "avatarUrl": ""},
                "search": {"issueCount": 1, "nodes": [pr]},
            }

        monkeypatch.setattr(pr_triage, "run_search", fake_run)
        monkeypatch.setattr(pr_triage, "list_worktrees", list)
        monkeypatch.setattr(pr_triage, "merge_method", lambda: "SQUASH")
        monkeypatch.setattr(pr_triage.time, "sleep", lambda s: None)

        result = pr_triage.fetch_prs("is:open")

        assert len(calls) == 2  # exactly one retry, not a loop
        assert result["prs"][0]["canMerge"] is True


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
        def payload(mergeable, state, decision="APPROVED"):
            pr = make_pr(decision=decision) | {
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

        def run(mergeable, state, decision="APPROVED"):
            monkeypatch.setattr(
                pr_triage,
                "sh",
                lambda args, **k: (0, json.dumps(payload(mergeable, state, decision)), ""),
            )
            monkeypatch.setattr(pr_triage, "merge_method", lambda: "SQUASH")
            return pr_triage.fetch_prs("is:open")["prs"][0]["canMerge"]

        assert run("MERGEABLE", "CLEAN") is True
        # GitHub reports BEHIND only when being out of date blocks the merge,
        # so a button there would be one the API refuses.
        assert run("MERGEABLE", "BEHIND") is False
        assert run("MERGEABLE", "BLOCKED") is False  # a required check or review is missing
        assert run("CONFLICTING", "DIRTY") is False
        assert run("UNKNOWN", "UNKNOWN") is False  # GitHub has not worked it out yet

        # Approval is required on its own. mergeStateStatus reports a single
        # reason, so a PR that is both behind its base and blocked by a
        # changes-requested review comes back BEHIND — offering merge would
        # send the user to a button GitHub refuses.
        assert run("MERGEABLE", "BEHIND", "CHANGES_REQUESTED") is False
        assert run("MERGEABLE", "CLEAN", "CHANGES_REQUESTED") is False
        assert run("MERGEABLE", "CLEAN", "REVIEW_REQUIRED") is False
        assert run("MERGEABLE", "CLEAN", None) is False
