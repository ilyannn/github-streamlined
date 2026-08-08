"""Task-list parsing and the checkbox rewrite that goes back to GitHub."""

from pathlib import Path

import pr_triage
import pytest
from pr_triage import count_tasks, parse_tasks, toggle_task

BODY = """\
## What

Some prose with [a link] and a - [ ] that is not a bullet.

- [x] first, done
- [ ] second, open
* [X] third, capital X
  - [ ] fourth, indented
"""


class TestParseTasks:
    def test_finds_every_checkbox(self):
        assert [(t["index"], t["done"], t["text"]) for t in parse_tasks(BODY)] == [
            (0, True, "first, done"),
            (1, False, "second, open"),
            (2, True, "third, capital X"),
            (3, False, "fourth, indented"),
        ]

    def test_empty_body(self):
        assert parse_tasks(None) == []
        assert parse_tasks("") == []

    def test_count_agrees_with_parse(self):
        assert count_tasks(BODY) == (2, 4)


class TestToggleTask:
    def test_ticks_the_named_box_only(self):
        updated = toggle_task(BODY, 1, True)
        assert [t["done"] for t in parse_tasks(updated)] == [True, True, True, False]

    def test_unticks(self):
        updated = toggle_task(BODY, 0, False)
        assert [t["done"] for t in parse_tasks(updated)] == [False, False, True, False]

    def test_leaves_the_rest_of_the_body_alone(self):
        updated = toggle_task(BODY, 3, True)
        # Only the one character inside the brackets may differ.
        assert len(updated) == len(BODY)
        assert sum(a != b for a, b in zip(updated, BODY, strict=True)) == 1
        assert updated.startswith("## What\n\nSome prose")
        assert "  - [x] fourth, indented" in updated

    def test_preserves_indentation_and_bullet_style(self):
        updated = toggle_task(BODY, 2, False)
        assert "* [ ] third, capital X" in updated

    def test_setting_an_already_set_box_is_a_no_op(self):
        assert toggle_task(BODY, 0, True) == BODY

    def test_rejects_an_index_that_is_not_there(self):
        with pytest.raises(RuntimeError, match="No task #9"):
            toggle_task(BODY, 9, True)


class TestSetTask:
    def test_writes_the_body_through_a_file(self, monkeypatch):
        calls = []
        seen = {}

        def fake_sh(args, **kwargs):
            calls.append(args)
            if args[:3] == ["gh", "pr", "view"]:
                return 0, BODY, ""
            # Capture what gh would have read, before the file is unlinked.
            seen["body"] = Path(args[args.index("--body-file") + 1]).read_text()
            return 0, "", ""

        monkeypatch.setattr(pr_triage, "sh", fake_sh)
        monkeypatch.setattr(pr_triage, "REPO", "acme/widgets")

        result = pr_triage.set_task(42, 1, True)

        assert [t["done"] for t in result["tasks"]] == [True, True, True, False]
        assert seen["body"] == toggle_task(BODY, 1, True)
        assert calls[1][:5] == ["gh", "pr", "edit", "42", "--repo"]

    def test_cleans_up_its_temp_file_when_gh_fails(self, monkeypatch):
        paths = []

        def fake_sh(args, **kwargs):
            if args[:3] == ["gh", "pr", "view"]:
                return 0, BODY, ""
            paths.append(args[args.index("--body-file") + 1])
            return 1, "", "gh exploded"

        monkeypatch.setattr(pr_triage, "sh", fake_sh)
        monkeypatch.setattr(pr_triage, "REPO", "acme/widgets")

        with pytest.raises(RuntimeError, match="gh exploded"):
            pr_triage.set_task(42, 1, True)
        import os

        assert not os.path.exists(paths[0])
