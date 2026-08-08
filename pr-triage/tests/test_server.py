"""HTTP layer tests: routing, status codes, request validation."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pr_triage
import pytest


@pytest.fixture
def base_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), pr_triage.Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def req(url, method="GET", body=None):
    request = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode()


def test_index_serves_html(base_url):
    status, body = req(base_url + "/")
    assert status == 200
    assert "PR Triage" in body


def test_serves_the_query_module(base_url):
    # index.html imports this; a 404 here would break every filter button.
    status, body = req(base_url + "/query.mjs")
    assert status == 200
    assert "export function toggleTerm" in body


def test_static_map_does_not_serve_arbitrary_files(base_url):
    for path in ("/pr_triage.py", "/../pyproject.toml", "/tests/test_server.py"):
        assert req(base_url + path)[0] == 404


def test_prs_uses_default_query(base_url, monkeypatch):
    monkeypatch.setattr(pr_triage, "fetch_prs", lambda q: {"query": q, "prs": []})
    status, body = req(base_url + "/api/prs")
    assert status == 200
    assert body["query"] == pr_triage.DEFAULT_QUERY


def test_prs_passes_query_through(base_url, monkeypatch):
    monkeypatch.setattr(pr_triage, "fetch_prs", lambda q: {"query": q, "prs": []})
    status, body = req(base_url + "/api/prs?q=is%3Aopen%20label%3A%22x%20y%22")
    assert status == 200
    assert body["query"] == 'is:open label:"x y"'


def test_fetch_failure_returns_500(base_url, monkeypatch):
    def boom(q):
        raise RuntimeError("gh exploded")

    monkeypatch.setattr(pr_triage, "fetch_prs", boom)
    status, body = req(base_url + "/api/prs")
    assert status == 500
    assert body["error"] == "gh exploded"


def test_unknown_paths_are_404(base_url):
    assert req(base_url + "/nope")[0] == 404
    assert req(base_url + "/api/nope", method="POST", body={"number": 1})[0] == 404


def test_non_numeric_pr_number_is_400(base_url):
    status, body = req(base_url + "/api/checkout", method="POST", body={"number": "abc"})
    assert status == 400
    assert "bad request" in body["error"]


def test_missing_number_is_400(base_url):
    assert req(base_url + "/api/labels", method="POST", body={})[0] == 400


def test_checkout_routes_to_do_checkout(base_url, monkeypatch):
    monkeypatch.setattr(pr_triage, "do_checkout", lambda n: {"path": "/repo", "branch": f"pr-{n}"})
    status, body = req(base_url + "/api/checkout", method="POST", body={"number": 5})
    assert status == 200
    assert body == {"path": "/repo", "branch": "pr-5"}


def test_labels_post_routes_to_do_edit(base_url, monkeypatch):
    calls = []
    monkeypatch.setattr(
        pr_triage,
        "do_edit",
        lambda number, af, rf, add, remove, *rest: (
            calls.append((number, add, remove)),
            {"ok": True},
        )[1],
    )
    status, body = req(
        base_url + "/api/labels", method="POST", body={"number": 5, "add": ["a"], "remove": ["b"]}
    )
    assert status == 200
    assert body == {"ok": True}
    assert calls == [(5, ["a"], ["b"])]
