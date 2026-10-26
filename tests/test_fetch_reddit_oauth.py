"""Reddit app-only OAuth in fetch_reddit (#328)."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import httpx
import pytest

import fetch
from fetch import fetch_reddit

SOURCE = {
    "id": "prog",
    "url": "https://www.reddit.com/r/programming/top.json?t=day",
    "min_score": 100,
}


def _post(title, score):
    return {"data": {
        "url_overridden_by_dest": f"https://example.com/{title}", "title": title,
        "score": score, "num_comments": 1, "subreddit": "programming",
        "author": "a", "created_utc": 1720519200.0, "selftext": "",
        "permalink": "/r/programming/comments/x/",
    }}


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload or {}
    if status >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=r)
    return r


LISTING = {"data": {"children": [_post("keep", 150), _post("drop", 50)]}}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(fetch, "_reddit_token", None)
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("REDDIT_USERNAME", raising=False)


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("REDDIT_USERNAME", "bob")


def test_authenticated_listing_rewrites_url_sets_headers_and_gates_score(creds):
    client = MagicMock()
    client.post.return_value = _resp(200, {"access_token": "tok"})
    client.get.return_value = _resp(200, LISTING)

    items = list(fetch_reddit(SOURCE, client=client))

    assert [i.title for i in items] == ["keep"]
    args, kwargs = client.post.call_args
    assert args[0] == "https://www.reddit.com/api/v1/access_token"
    assert kwargs["auth"] == ("cid", "secret")
    assert kwargs["data"] == {"grant_type": "client_credentials"}
    assert kwargs["headers"]["User-Agent"] == "python:agent-newsletter:v0.1 (by /u/bob)"
    args, kwargs = client.get.call_args
    assert args[0] == "https://oauth.reddit.com/r/programming/top?t=day"
    assert kwargs["headers"]["Authorization"] == "bearer tok"
    assert "by /u/bob" in kwargs["headers"]["User-Agent"]


def test_token_is_fetched_once_per_run(creds):
    client = MagicMock()
    client.post.return_value = _resp(200, {"access_token": "tok"})
    client.get.return_value = _resp(200, LISTING)
    list(fetch_reddit(SOURCE, client=client))
    list(fetch_reddit(SOURCE, client=client))
    assert client.post.call_count == 1
    assert client.get.call_count == 2


def test_default_username_in_user_agent(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    assert fetch._reddit_user_agent() == "python:agent-newsletter:v0.1 (by /u/agent-newsletter)"


def test_401_refreshes_token_once_and_retries(creds):
    client = MagicMock()
    client.post.side_effect = [_resp(200, {"access_token": "old"}),
                               _resp(200, {"access_token": "new"})]
    client.get.side_effect = [_resp(401), _resp(200, LISTING)]

    items = list(fetch_reddit(SOURCE, client=client))

    assert [i.title for i in items] == ["keep"]
    assert client.post.call_count == 2
    assert client.get.call_args.kwargs["headers"]["Authorization"] == "bearer new"


def test_second_401_is_a_collector_failure(creds):
    client = MagicMock()
    client.post.return_value = _resp(200, {"access_token": "t"})
    client.get.return_value = _resp(401)
    with pytest.raises(httpx.HTTPStatusError):
        list(fetch_reddit(SOURCE, client=client))
    assert client.post.call_count == 2
    assert client.get.call_count == 2


def test_429_is_a_normal_collector_failure(creds):
    client = MagicMock()
    client.post.return_value = _resp(200, {"access_token": "t"})
    client.get.return_value = _resp(429)
    with pytest.raises(httpx.HTTPStatusError):
        list(fetch_reddit(SOURCE, client=client))
    assert client.get.call_count == 1


def test_token_response_without_access_token_fails(creds):
    client = MagicMock()
    client.post.return_value = _resp(200, {"error": "invalid_grant"})
    with pytest.raises(RuntimeError, match="access_token"):
        list(fetch_reddit(SOURCE, client=client))


def test_token_http_error_propagates(creds):
    client = MagicMock()
    client.post.return_value = _resp(401)
    with pytest.raises(httpx.HTTPStatusError):
        list(fetch_reddit(SOURCE, client=client))


def test_unauthenticated_path_is_unchanged_without_credentials():
    client = MagicMock()
    client.get.return_value = _resp(200, LISTING)
    items = list(fetch_reddit(SOURCE, client=client))
    assert [i.title for i in items] == ["keep"]
    client.post.assert_not_called()
    assert client.get.call_args.args == (SOURCE["url"],)


def test_partial_credentials_count_as_unset(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    assert fetch.reddit_credentials() is None


def test_non_reddit_url_is_not_rewritten():
    assert fetch._reddit_oauth_url("https://example.com/r/x/top.json") == "https://example.com/r/x/top.json"


def test_url_without_json_suffix_is_rewritten():
    assert fetch._reddit_oauth_url("https://old.reddit.com/r/x/top?t=day") == "https://oauth.reddit.com/r/x/top?t=day"


def test_no_credentials_skips_reddit_without_http_or_failure(caplog):
    client = MagicMock()
    sources = {"reddit": [SOURCE, {**SOURCE, "id": "two"}]}
    with caplog.at_level(logging.INFO):
        collectors = list(fetch.iter_collectors(sources, client))
    assert collectors == []
    client.get.assert_not_called()
    client.post.assert_not_called()
    msgs = [r.getMessage() for r in caplog.records if "reddit" in r.getMessage()]
    assert len(msgs) == 1 and "skipped" in msgs[0] and "REDDIT_CLIENT_ID" in msgs[0]


def test_credentials_present_yields_reddit_collectors(creds):
    collectors = list(fetch.iter_collectors({"reddit": [SOURCE]}, MagicMock()))
    assert [label for label, _ in collectors] == ["reddit/prog"]
