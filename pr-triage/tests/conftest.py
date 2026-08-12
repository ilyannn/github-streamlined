import sys
from pathlib import Path

import pytest

# The tool lives in a hyphenated directory, so it is not importable as a
# package; put it on sys.path instead.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _forget_merge_state(monkeypatch):
    """Mergeability is cached in a module global; keep it out of other tests."""
    import pr_triage

    monkeypatch.setattr(pr_triage, "_merge_state", {})
