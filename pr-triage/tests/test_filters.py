"""Bot and CI-command noise, and the path: filter GitHub search cannot do."""

import json

import pr_triage
from pr_triage import (
    classify,
    is_noise,
    path_matches,
    split_path_terms,
    touches_paths,
)
from test_pr_triage import ME, make_pr, t


def with_comment(pr, login, at, body="", typename="User"):
    pr["comments"]["nodes"].append(
        {"author": {"login": login, "__typename": typename}, "createdAt": at, "bodyText": body}
    )
    return pr


class TestNoise:
    def test_bot_accounts_are_noise(self):
        assert is_noise({"author": {"login": "github-actions", "__typename": "Bot"}})
        # Ordinary user accounts that are bots in all but name.
        assert is_noise({"author": {"login": "codecov-commenter", "__typename": "User"}})

    def test_ci_commands_are_noise(self):
        for body in ["/test", "/ci", "/test all", "/backport 8.x", "@copilot review", "/retest  "]:
            assert is_noise({"author": {"login": "bob"}, "bodyText": body}), body

    def test_people_saying_things_are_not(self):
        assert not is_noise({"author": {"login": "bob"}, "bodyText": "LGTM /test later?"})
        assert not is_noise({"author": {"login": "bob"}, "bodyText": "Looks good"})

    def test_bot_comment_does_not_bring_a_pr_back(self):
        pr = make_pr(reviews=[(ME, "COMMENTED", t(2))], commit_at=t(1))
        with_comment(pr, "github-actions", t(3), "Coverage report", typename="Bot")
        with_comment(pr, "bob", t(4), "/test")
        bucket, badges = classify(pr, ME)
        assert bucket == "waiting"
        assert not any("new comment" in b["text"] for b in badges)

    def test_a_person_still_does(self):
        pr = make_pr(reviews=[(ME, "COMMENTED", t(2))], commit_at=t(1))
        with_comment(pr, "bob", t(3), "Could you look again?")
        bucket, badges = classify(pr, ME)
        assert bucket == "review"
        assert {"text": "1 new comment", "kind": "warn"} in badges

    def test_bot_reviews_do_not_count(self):
        pr = make_pr(reviews=[(ME, "COMMENTED", t(2))], commit_at=t(1))
        pr["reviews"]["nodes"].append(
            {
                "author": {"login": "copilot-pull-request-reviewer", "__typename": "Bot"},
                "state": "COMMENTED",
                "submittedAt": t(3),
            }
        )
        assert classify(pr, ME)[0] == "waiting"


class TestPathTerms:
    def test_takes_path_terms_out_of_the_query(self):
        assert split_path_terms('is:open path:src/* -path:"docs/a b/*" label:x') == (
            "is:open label:x",
            ["src/*"],
            ["docs/a b/*"],
        )

    def test_leaves_other_queries_exactly_alone(self):
        query = "is:open  (involves:@me OR review-requested:@me)"
        assert split_path_terms(query) == (query, [], [])

    def test_globs_cross_directories(self):
        assert path_matches("src/linux/a/b.c", "src/linux/*")
        assert path_matches("src/linux/a/b.c", "*.c")
        assert not path_matches("src/mac/b.c", "src/linux/*")

    def test_a_plain_path_covers_what_is_under_it(self):
        assert path_matches("src/linux/a.c", "src/linux")
        assert path_matches("src/linux/a.c", "src/linux/")
        assert path_matches("src/linux", "src/linux")
        assert not path_matches("src/linuxish/a.c", "src/linux")

    def test_include_is_any_and_exclude_is_none(self):
        pr = {"files": ["src/a.py", "docs/b.md"]}
        assert touches_paths(pr, ["src"], [])
        assert touches_paths(pr, ["lib", "docs/*"], [])
        assert not touches_paths(pr, ["lib"], [])
        assert not touches_paths(pr, [], ["docs"])
        assert touches_paths(pr, ["src"], ["test"])

    def test_fetch_prs_asks_for_files_only_when_filtering(self, monkeypatch):
        def pr(number):
            return make_pr() | {
                "number": number,
                "title": "t",
                "url": "u",
                "body": "",
                "createdAt": t(0),
                "updatedAt": t(1),
                "headRefName": f"b{number}",
                "additions": 0,
                "deletions": 0,
                "labels": {"nodes": []},
                "assignees": {"nodes": []},
            }

        searches, asked = [], []
        payload = {
            "viewer": {"login": ME, "avatarUrl": ""},
            "search": {"issueCount": 2, "nodes": [pr(1), pr(2)]},
        }
        monkeypatch.setattr(
            pr_triage,
            "run_search",
            lambda q: (searches.append(q), json.loads(json.dumps(payload)))[1],
        )
        monkeypatch.setattr(
            pr_triage,
            "files_chunk",
            lambda ns: (asked.append(list(ns)), {1: ["src/a.py"], 2: ["x.md"]})[1],
        )
        monkeypatch.setattr(pr_triage, "REPO", "acme/widgets")
        monkeypatch.setattr(pr_triage, "merge_state_for", lambda ns: {})
        monkeypatch.setattr(pr_triage, "list_worktrees", list)
        monkeypatch.setattr(pr_triage, "merge_method", lambda: "SQUASH")

        out = pr_triage.fetch_prs("is:open path:src")
        assert [p["number"] for p in out["prs"]] == [1]
        assert searches == ["repo:acme/widgets is:pr is:open"]
        assert out["query"] == "is:open path:src"
        assert asked == [[1, 2]]

        # Nothing moved, so the file lists are remembered.
        pr_triage.fetch_prs("is:open -path:src")
        assert asked == [[1, 2]]

        pr_triage.fetch_prs("is:open")
        assert asked == [[1, 2]]
