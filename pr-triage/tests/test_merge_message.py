"""The default commit message, composed the way each repo setting says."""

import json

import pr_triage
import pytest

PR = {
    "title": "Add the frobnicator",
    "body": "Why this exists.\n\n- [x] tested",
    "number": 42,
    "headRefName": "feat/frob",
    "commits": [
        {"messageHeadline": "First step", "messageBody": "with detail"},
        {"messageHeadline": "Second step", "messageBody": ""},
    ],
}


@pytest.fixture(autouse=True)
def repo(monkeypatch):
    monkeypatch.setattr(pr_triage, "REPO", "acme/widgets")
    monkeypatch.setattr(pr_triage, "_commit_config", None)


def wire(monkeypatch, method, config, pr=None):
    monkeypatch.setattr(pr_triage, "_merge_method", method)

    def fake_sh(args, **kwargs):
        if args[:2] == ["gh", "api"]:
            return 0, json.dumps(config), ""
        if args[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(pr or PR), ""
        raise AssertionError(f"unexpected: {args}")

    monkeypatch.setattr(pr_triage, "sh", fake_sh)


SQUASH_PR_BODY = {"squashTitle": "PR_TITLE", "squashMessage": "PR_BODY"}


def test_a_trailing_space_in_the_title_is_trimmed(monkeypatch):
    wire(monkeypatch, "SQUASH", SQUASH_PR_BODY, PR | {"title": "Add the frobnicator "})
    assert pr_triage.merge_message(42)["subject"] == "Add the frobnicator (#42)"


def test_squash_from_pr_title_and_body(monkeypatch):
    wire(monkeypatch, "SQUASH", SQUASH_PR_BODY)
    message = pr_triage.merge_message(42)
    assert message["subject"] == "Add the frobnicator (#42)"
    assert message["body"] == "Why this exists.\n\n- [x] tested"
    assert message["editable"] is True


def test_squash_from_commit_messages(monkeypatch):
    wire(monkeypatch, "SQUASH", {"squashTitle": "PR_TITLE", "squashMessage": "COMMIT_MESSAGES"})
    assert pr_triage.merge_message(42)["body"] == "First step\n\nwith detail\n\nSecond step"


def test_squash_with_a_blank_message(monkeypatch):
    wire(monkeypatch, "SQUASH", {"squashTitle": "PR_TITLE", "squashMessage": "BLANK"})
    assert pr_triage.merge_message(42)["body"] == ""


def test_squash_uses_the_lone_commit_headline_when_configured(monkeypatch):
    single = PR | {"commits": [{"messageHeadline": "Just the one", "messageBody": ""}]}
    wire(monkeypatch, "SQUASH", {"squashTitle": "COMMIT_OR_PR_TITLE"}, single)
    assert pr_triage.merge_message(42)["subject"] == "Just the one"


def test_commit_or_pr_title_falls_back_with_several_commits(monkeypatch):
    wire(monkeypatch, "SQUASH", {"squashTitle": "COMMIT_OR_PR_TITLE"}, PR)
    assert pr_triage.merge_message(42)["subject"] == "Add the frobnicator (#42)"


def test_merge_commit_default_subject(monkeypatch):
    wire(monkeypatch, "MERGE", {"mergeTitle": "MERGE_MESSAGE", "mergeMessage": "PR_TITLE"})
    message = pr_triage.merge_message(42)
    assert message["subject"] == "Merge pull request #42 from feat/frob"
    assert message["body"] == "Add the frobnicator"


def test_merge_commit_with_pr_title_subject(monkeypatch):
    wire(monkeypatch, "MERGE", {"mergeTitle": "PR_TITLE", "mergeMessage": "PR_BODY"})
    message = pr_triage.merge_message(42)
    assert message["subject"] == "Add the frobnicator (#42)"
    assert message["body"] == "Why this exists.\n\n- [x] tested"


def test_rebase_has_no_message_to_edit(monkeypatch):
    monkeypatch.setattr(pr_triage, "_merge_method", "REBASE")
    monkeypatch.setattr(pr_triage, "sh", lambda *a, **k: pytest.fail("should not ask gh"))
    assert pr_triage.merge_message(42)["editable"] is False


def test_config_is_fetched_once(monkeypatch):
    wire(monkeypatch, "SQUASH", SQUASH_PR_BODY)
    calls = []
    original = pr_triage.sh
    monkeypatch.setattr(
        pr_triage, "sh", lambda args, **k: (calls.append(args[:2]), original(args, **k))[1]
    )
    pr_triage.merge_message(42)
    pr_triage.merge_message(42)
    assert calls.count(["gh", "api"]) == 1
