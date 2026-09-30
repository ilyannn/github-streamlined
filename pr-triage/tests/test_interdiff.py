"""The since-your-review diff: picking the baseline, fetching blobs, diffing."""

import json

import pr_triage
import pytest
from pr_triage import diff_file, last_reviewed_commit, make_patch
from test_pr_triage import ME, t


def blob(text, binary=False):
    return {"text": None if binary else text, "binary": binary}


@pytest.fixture(autouse=True)
def _forget_blobs(monkeypatch):
    monkeypatch.setattr(pr_triage, "_blobs", {})
    monkeypatch.setattr(pr_triage, "REPO", "acme/widgets")


class TestBaseline:
    def review(self, login, at, oid):
        return {"author": {"login": login}, "submittedAt": at, "commit": {"oid": oid}}

    def test_latest_of_your_reviews(self):
        reviews = [
            self.review(ME, t(1), "aaa"),
            self.review("bob", t(5), "bbb"),
            self.review(ME, t(3), "ccc"),
        ]
        assert last_reviewed_commit(reviews, ME) == ("ccc", t(3))

    def test_pending_reviews_do_not_count(self):
        reviews = [self.review(ME, t(1), "aaa"), self.review(ME, None, "ccc")]
        assert last_reviewed_commit(reviews, ME) == ("aaa", t(1))

    def test_none_when_you_never_reviewed(self):
        assert last_reviewed_commit([self.review("bob", t(1), "a")], ME) is None


class TestPatch:
    def test_hunks_without_file_headers(self):
        patch = make_patch("a\nb\nc\n", "a\nB\nc\n")
        assert patch.splitlines() == ["@@ -1,3 +1,3 @@", " a", "-b", "+B", " c"]

    def test_keeps_lines_that_look_like_headers(self):
        # A removed "-- x" line is "--- x" in the patch; it must survive.
        patch = make_patch("keep\n-- x\n", "keep\n")
        assert "--- x" in patch.splitlines()

    def test_file_statuses(self):
        assert diff_file("a", None, blob("x"), blob("x"))["status"] == "unchanged"
        assert diff_file("a", None, None, blob("x\n"))["status"] == "added"
        assert diff_file("a", None, blob("x\n"), None)["status"] == "removed"
        assert diff_file("a", None, blob("", True), blob("", True))["status"] == "unchanged"
        assert diff_file("a", None, blob("x"), blob("", True))["status"] == "binary"

        changed = diff_file("a", None, blob("x\ny\n"), blob("x\nz\nw\n"))
        assert (changed["status"], changed["additions"], changed["deletions"]) == (
            "modified",
            2,
            1,
        )


class TestFetchInterdiff:
    def fake_gh(self, monkeypatch, files, blobs, head="head1", base="base1"):
        """Answer the three kinds of call fetch_interdiff makes."""
        calls = []

        def fake_sh(args, **kwargs):
            calls.append(args)
            if "--paginate" in args:
                return 0, "\n".join(json.dumps(f) for f in files), ""
            query = next(a for a in args if a.startswith("query="))
            if "viewer" in query:
                pr = {
                    "title": "T",
                    "url": "https://github.com/acme/widgets/pull/7",
                    "headRefOid": head,
                    "reviews": {
                        "nodes": [
                            {"author": {"login": ME}, "submittedAt": t(1), "commit": {"oid": base}}
                        ]
                    },
                }
                data = {"viewer": {"login": ME}, "repository": {"pullRequest": pr}}
                return 0, json.dumps({"data": data}), ""
            repo = {}
            for i, (sha, path) in enumerate(blob_keys(query)):
                text = blobs.get((sha, path))
                repo[f"b{i}"] = None if text is None else {"text": text, "isBinary": False}
            return 0, json.dumps({"data": {"repository": repo}}), ""

        monkeypatch.setattr(pr_triage, "sh", fake_sh)
        return calls

    def test_diffs_each_file_between_reviewed_commit_and_head(self, monkeypatch):
        self.fake_gh(
            monkeypatch,
            files=[["a.py", None], ["same.py", None], ["new.py", "old.py"]],
            blobs={
                ("base1", "a.py"): "one\n",
                ("head1", "a.py"): "two\n",
                ("base1", "same.py"): "s\n",
                ("head1", "same.py"): "s\n",
                ("base1", "old.py"): "r\n",
                ("head1", "new.py"): "r\nmore\n",
            },
        )
        out = pr_triage.fetch_interdiff(7)
        assert (out["base"], out["head"]) == ("base1", "head1")
        assert out["githubUrl"] == "https://github.com/acme/widgets/pull/7/files/base1..head1"
        by_path = {f["path"]: f for f in out["files"]}
        assert by_path["a.py"]["status"] == "modified"
        # Renamed after the review: compared against its old name, not "added".
        assert by_path["new.py"]["status"] == "modified"
        assert by_path["new.py"]["additions"] == 1
        assert out["files"][-1]["path"] == "same.py"  # unchanged goes last

    def test_nothing_pushed_asks_for_no_files(self, monkeypatch):
        calls = self.fake_gh(monkeypatch, files=[], blobs={}, head="same", base="same")
        out = pr_triage.fetch_interdiff(7)
        assert out["files"] == []
        assert len(calls) == 1

    def test_blobs_are_fetched_once(self, monkeypatch):
        calls = self.fake_gh(
            monkeypatch,
            files=[["a.py", None]],
            blobs={("base1", "a.py"): "1\n", ("head1", "a.py"): "2\n"},
        )
        pr_triage.fetch_interdiff(7)
        first = len(calls)
        pr_triage.fetch_interdiff(7)
        # The PR and its files are asked again; the blobs are not.
        assert len(calls) == first * 2 - 1


def blob_keys(query):
    """The (sha, path) pairs a blob query asks for, in alias order."""
    keys = []
    for line in query.splitlines():
        if "object(expression:" in line:
            expression = json.loads(line.split("expression: ", 1)[1].split(")", 1)[0])
            sha, _, path = expression.partition(":")
            keys.append((sha, path))
    return keys
