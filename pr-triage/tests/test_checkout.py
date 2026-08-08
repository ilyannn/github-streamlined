"""Tests for worktree-based checkout: reuse, creation, and cleanup on failure."""

import pr_triage
import pytest
from pr_triage import do_checkout, parse_worktrees, worktree_dir

PORCELAIN = """\
worktree /repo
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree /repo/.worktrees/pr-42
HEAD 2222222222222222222222222222222222222222
branch refs/heads/feature/frob

worktree /repo/.worktrees/pr-7
HEAD 3333333333333333333333333333333333333333
detached
"""


class FakeShell:
    """Stands in for sh(), matching on the first few words of each command."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, args, cwd=None, **kwargs):
        self.calls.append((args, cwd))
        for prefix, response in self.responses.items():
            if args[: len(prefix)] == list(prefix):
                return response
        raise AssertionError(f"unexpected command: {args}")

    def ran(self, *prefix):
        return [(a, c) for a, c in self.calls if a[: len(prefix)] == list(prefix)]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(pr_triage, "MAIN_CHECKOUT", str(tmp_path / "repo"))
    monkeypatch.setattr(pr_triage, "REPO", "acme/widgets")
    monkeypatch.delenv("PR_TRIAGE_WORKTREES", raising=False)
    return tmp_path / "repo"


def shell_for(monkeypatch, head_branch, worktrees, checkout=(0, "", "")):
    fake = FakeShell(
        {
            ("gh", "pr", "view"): (0, head_branch + "\n", ""),
            ("git", "worktree", "list"): (0, worktrees, ""),
            ("git", "worktree", "add"): (0, "", ""),
            ("git", "worktree", "remove"): (0, "", ""),
            ("gh", "pr", "checkout"): checkout,
        }
    )
    monkeypatch.setattr(pr_triage, "sh", fake)
    return fake


class TestParseWorktrees:
    def test_parses_paths_and_branches(self):
        assert parse_worktrees(PORCELAIN) == [
            ("/repo", "main"),
            ("/repo/.worktrees/pr-42", "feature/frob"),
            ("/repo/.worktrees/pr-7", None),
        ]

    def test_empty_input(self):
        assert parse_worktrees("") == []


class TestWorktreeDir:
    def test_defaults_beside_the_checkout(self, repo):
        assert worktree_dir(42) == repo / ".worktrees" / "pr-42"

    def test_env_override(self, repo, monkeypatch, tmp_path):
        monkeypatch.setenv("PR_TRIAGE_WORKTREES", str(tmp_path / "elsewhere"))
        assert worktree_dir(42) == tmp_path / "elsewhere" / "pr-42"


class TestDoCheckout:
    def test_reuses_a_worktree_on_the_same_branch(self, repo, monkeypatch):
        fake = shell_for(monkeypatch, "feature/frob", PORCELAIN.replace("/repo", str(repo)))
        result = do_checkout(99)
        assert result == {
            "path": str(repo / ".worktrees/pr-42"),
            "branch": "feature/frob",
            "created": False,
        }
        assert not fake.ran("git", "worktree", "add")

    def test_reuses_the_target_directory_even_when_detached(self, repo, monkeypatch):
        # pr-7 is checked out detached, so the branch never matches; the path does.
        fake = shell_for(monkeypatch, "some/branch", PORCELAIN.replace("/repo", str(repo)))
        result = do_checkout(7)
        assert result["path"] == str(repo / ".worktrees/pr-7")
        assert result["created"] is False
        assert not fake.ran("git", "worktree", "add")

    def test_creates_a_worktree_when_none_exists(self, repo, monkeypatch):
        fake = shell_for(monkeypatch, "feature/new", PORCELAIN.replace("/repo", str(repo)))
        target = repo / ".worktrees" / "pr-100"

        result = do_checkout(100)

        assert result == {"path": str(target), "branch": "feature/new", "created": True}
        [(add_args, add_cwd)] = fake.ran("git", "worktree", "add")
        assert add_args == ["git", "worktree", "add", "--detach", str(target)]
        assert add_cwd == str(repo)
        # gh runs inside the new worktree, so it checks the PR out there.
        [(co_args, co_cwd)] = fake.ran("gh", "pr", "checkout")
        assert co_args == ["gh", "pr", "checkout", "100", "--repo", "acme/widgets"]
        assert co_cwd == str(target)
        assert target.parent.is_dir()

    def test_removes_the_worktree_when_checkout_fails(self, repo, monkeypatch):
        fake = shell_for(
            monkeypatch,
            "feature/new",
            PORCELAIN.replace("/repo", str(repo)),
            checkout=(1, "", "no such pull request"),
        )
        with pytest.raises(RuntimeError, match="no such pull request"):
            do_checkout(100)
        # A half-made detached worktree would block the next attempt.
        [(rm_args, _)] = fake.ran("git", "worktree", "remove")
        assert rm_args == [
            "git",
            "worktree",
            "remove",
            "--force",
            str(repo / ".worktrees" / "pr-100"),
        ]

    def test_never_touches_the_main_checkout(self, repo, monkeypatch):
        fake = shell_for(monkeypatch, "feature/new", PORCELAIN.replace("/repo", str(repo)))
        do_checkout(100)
        # The old behaviour switched branches in the main checkout; nothing may
        # mutate it now, so a dirty main checkout can no longer block a review.
        mutating = [a for a, _ in fake.calls if a[:2] == ["gh", "pr"] and a[2] == "checkout"]
        assert all(cwd != str(repo) for _, cwd in fake.ran("gh", "pr", "checkout"))
        assert mutating and not fake.ran("git", "checkout")

    def test_without_a_checkout_configured(self, monkeypatch):
        monkeypatch.setattr(pr_triage, "MAIN_CHECKOUT", None)
        with pytest.raises(RuntimeError, match="No checkout configured"):
            do_checkout(1)

    def test_opens_a_new_worktree_with_the_given_command(self, repo, monkeypatch):
        shell_for(monkeypatch, "feature/new", PORCELAIN.replace("/repo", str(repo)))
        spawned = []
        monkeypatch.setattr(pr_triage, "spawn", spawned.append)
        monkeypatch.setattr(pr_triage.shutil, "which", lambda name: f"/usr/bin/{name}")

        result = do_checkout(100, "code")

        assert result["opened"] == "code"
        assert spawned == [["/usr/bin/code", str(repo / ".worktrees" / "pr-100")]]

    def test_opens_an_existing_worktree_too(self, repo, monkeypatch):
        fake = shell_for(monkeypatch, "feature/frob", PORCELAIN.replace("/repo", str(repo)))
        spawned = []
        monkeypatch.setattr(pr_triage, "spawn", spawned.append)
        monkeypatch.setattr(pr_triage.shutil, "which", lambda name: f"/usr/bin/{name}")

        result = do_checkout(99, "code")

        assert result["created"] is False
        assert spawned == [["/usr/bin/code", str(repo / ".worktrees/pr-42")]]
        assert not fake.ran("git", "worktree", "add")

    def test_no_command_means_no_process(self, repo, monkeypatch):
        shell_for(monkeypatch, "feature/frob", PORCELAIN.replace("/repo", str(repo)))
        monkeypatch.setattr(pr_triage, "spawn", lambda argv: pytest.fail("spawned"))
        assert "opened" not in do_checkout(99)


class TestOpenPath:
    def test_appends_the_path_as_its_own_argument(self, monkeypatch):
        spawned = []
        monkeypatch.setattr(pr_triage, "spawn", spawned.append)
        monkeypatch.setattr(pr_triage.shutil, "which", lambda name: f"/bin/{name}")
        pr_triage.open_path("code", "/tmp/pr 42")
        # A space in the path must stay one argument, not become two.
        assert spawned == [["/bin/code", "/tmp/pr 42"]]

    def test_supports_a_command_with_arguments(self, monkeypatch):
        spawned = []
        monkeypatch.setattr(pr_triage, "spawn", spawned.append)
        monkeypatch.setattr(pr_triage.shutil, "which", lambda name: f"/usr/bin/{name}")
        pr_triage.open_path('open -a "Visual Studio Code"', "/tmp/wt")
        assert spawned == [["/usr/bin/open", "-a", "Visual Studio Code", "/tmp/wt"]]

    def test_reports_a_command_that_is_not_installed(self, monkeypatch):
        monkeypatch.setattr(pr_triage.shutil, "which", lambda name: None)
        monkeypatch.setattr(pr_triage, "spawn", lambda argv: pytest.fail("spawned"))
        with pytest.raises(RuntimeError, match="Command not found on PATH: nope"):
            pr_triage.open_path("nope", "/tmp/wt")

    def test_rejects_an_empty_command(self, monkeypatch):
        monkeypatch.setattr(pr_triage, "spawn", lambda argv: pytest.fail("spawned"))
        with pytest.raises(RuntimeError, match="No open command"):
            pr_triage.open_path("   ", "/tmp/wt")
