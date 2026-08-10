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
