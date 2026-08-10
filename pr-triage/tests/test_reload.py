"""Noticing that the code on disk has moved on."""

import pr_triage
import pytest


@pytest.fixture
def sources(tmp_path, monkeypatch):
    (tmp_path / "pr_triage.py").write_text("print('hi')\n")
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "query.mjs").write_text("export const a = 1;")
    monkeypatch.setattr(pr_triage, "HERE", tmp_path)
    return tmp_path


def touch(path, when):
    import os

    os.utime(path, (when, when))


class TestSourceStamps:
    def test_watches_python_only(self, sources):
        # The browser re-reads html and modules per request, so only Python
        # needs the process restarted.
        assert [p.name for p in pr_triage.source_stamps()] == ["pr_triage.py"]

    def test_nothing_changed_by_default(self, sources):
        assert pr_triage.changed_files(pr_triage.source_stamps()) == []

    def test_notices_an_edit(self, sources):
        stamps = pr_triage.source_stamps()
        touch(sources / "pr_triage.py", 1_000_000)
        assert [p.name for p in pr_triage.changed_files(stamps)] == ["pr_triage.py"]

    def test_ignores_a_file_that_vanished_mid_write(self, sources):
        stamps = pr_triage.source_stamps()
        (sources / "pr_triage.py").unlink()
        # A rename-into-place editor briefly leaves nothing there; restarting on
        # that would race the write.
        assert pr_triage.changed_files(stamps) == []


class TestUnparsable:
    """The guard that keeps a half-written save from killing the server."""

    def test_healthy_source_is_fine(self, sources):
        assert pr_triage.unparsable([sources / "pr_triage.py"]) == []

    def test_catches_a_syntax_error(self, sources):
        broken = sources / "pr_triage.py"
        broken.write_text("def half_written(:\n")
        assert pr_triage.unparsable([broken]) == [broken]

    def test_catches_a_truncated_file(self, sources):
        broken = sources / "pr_triage.py"
        broken.write_text("def f():\n    return {'a': 1,\n")
        assert pr_triage.unparsable([broken]) == [broken]

    def test_treats_a_vanished_file_as_broken(self, sources):
        gone = sources / "pr_triage.py"
        gone.unlink()
        # Restarting into a missing entry point would end the process.
        assert pr_triage.unparsable([gone]) == [gone]

    def test_reports_every_broken_file(self, sources):
        one, two = sources / "pr_triage.py", sources / "other.py"
        one.write_text("oops(")
        two.write_text("also bad(")
        assert set(pr_triage.unparsable([one, two])) == {one, two}


class TestWhyNotRestart:
    """Parse errors are the common case; import-time failures are the fatal one."""

    def test_silent_when_the_new_code_would_run(self, monkeypatch, sources):
        monkeypatch.setattr(pr_triage, "sh", lambda *a, **k: (0, "", ""))
        assert pr_triage.why_not_restart([sources / "pr_triage.py"]) is None

    def test_reports_a_parse_error_without_spawning_anything(self, monkeypatch, sources):
        broken = sources / "pr_triage.py"
        broken.write_text("nope(")
        monkeypatch.setattr(pr_triage, "sh", lambda *a, **k: pytest.fail("should not import"))
        assert "will not parse" in pr_triage.why_not_restart([broken])

    def test_reports_an_import_failure(self, monkeypatch, sources):
        # Parses fine, dies on exec — the case that would have lost the server.
        (sources / "pr_triage.py").write_text("undefined_name()\n")
        monkeypatch.setattr(
            pr_triage,
            "sh",
            lambda *a, **k: (
                1,
                "",
                "Traceback...\nNameError: name 'undefined_name' is not defined",
            ),
        )
        why = pr_triage.why_not_restart([sources / "pr_triage.py"])
        assert "fails on import" in why
        assert "NameError" in why

    def test_copes_with_an_empty_stderr(self, monkeypatch, sources):
        monkeypatch.setattr(pr_triage, "sh", lambda *a, **k: (1, "", ""))
        assert "unknown error" in pr_triage.why_not_restart([sources / "pr_triage.py"])


class TestAssetVersion:
    def test_changes_when_the_html_changes(self, sources):
        before = pr_triage.asset_version()
        touch(sources / "index.html", 1_000_000)
        assert pr_triage.asset_version() != before

    def test_changes_when_a_module_changes(self, sources):
        before = pr_triage.asset_version()
        touch(sources / "query.mjs", 1_000_000)
        assert pr_triage.asset_version() != before

    def test_steady_when_nothing_changes(self, sources):
        assert pr_triage.asset_version() == pr_triage.asset_version()

    def test_ignores_python_edits(self, sources):
        # Python changes restart the process instead; reloading the page for
        # them would be pointless.
        before = pr_triage.asset_version()
        touch(sources / "pr_triage.py", 1_000_000)
        assert pr_triage.asset_version() == before

    def test_notices_a_file_moving_backwards(self, sources):
        # Checking out an older revision leaves an older mtime; the version has
        # to change for that too, which is why it is a hash and not a maximum.
        before = pr_triage.asset_version()
        touch(sources / "index.html", 1_000_000)
        assert pr_triage.asset_version() != before

    def test_survives_an_empty_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pr_triage, "HERE", tmp_path)
        assert pr_triage.asset_version() == pr_triage.asset_version()
