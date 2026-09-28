import json

import pytest

import reddit_post_reader as rpr
from reddit_post_reader import Response

CID, SECRET, UA = "the-client-id", "the-client-secret", "linux:test:0.1.0 (by /u/tester)"
SHARE = "https://www.reddit.com/r/emacs/s/3Yk3i8s8oO"
POST = {"subreddit": "emacs", "title": "A nice trick", "author": "someone", "created_utc": 1790000000,
        "permalink": "/r/emacs/comments/1abc2de/a_nice_trick/", "is_self": True,
        "selftext": "Use `M-x` & friends.", "url": "https://www.reddit.com/r/emacs/comments/1abc2de/",
        "over_18": False}


class FakeReddit:
    """Answers the requests reddit_post_reader makes; records them."""

    def __init__(self, token_status=200, share_api=None, share_www=None, post=POST):
        self.calls = []
        self.token_status, self.post = token_status, post
        self.share = {rpr.API: share_api, rpr.WWW: share_www}

    def __call__(self, method, url, headers, data=None, follow=True):
        self.calls.append((method, url, headers, data, follow))
        if url == rpr.TOKEN_URL:
            body = json.dumps({"access_token": "tok123", "token_type": "bearer"}).encode()
            return Response(self.token_status, {}, body if self.token_status == 200 else b"{}")
        for base, target in self.share.items():
            if url.startswith(base + "/r/") and "/s/" in url:
                if target is None:
                    return Response(403, {}, b"Blocked")
                return Response(301, {"location": target}, b"")
        if url.startswith(rpr.API + "/api/info"):
            children = [{"kind": "t3", "data": self.post}] if self.post else []
            return Response(200, {}, json.dumps({"data": {"children": children}}).encode())
        return Response(404, {}, b"")


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        f = FakeReddit(**kw)
        monkeypatch.setattr(rpr, "_request", f)
        return f
    return install


@pytest.mark.parametrize("url,pid", [
    ("https://www.reddit.com/r/emacs/comments/1abc2de/a_nice_trick/", "1abc2de"),
    ("https://old.reddit.com/r/emacs/comments/1ABC2DE/", "1abc2de"),
    ("https://reddit.com/comments/1abc2de", "1abc2de"),
    ("https://redd.it/1abc2de", "1abc2de"),
    (SHARE, None),
    ("https://www.reddit.com/r/emacs/", None),
])
def test_post_id(url, pid):
    assert rpr.post_id(url) == pid


def test_share_path_and_host_check():
    assert rpr.share_path(SHARE) == "/r/emacs/s/3Yk3i8s8oO"
    assert rpr.share_path("https://www.reddit.com/r/emacs/comments/1abc2de/") is None
    assert rpr.is_reddit_url(SHARE) and rpr.is_reddit_url("https://redd.it/x")
    assert not rpr.is_reddit_url("https://reddit.com.evil.example/r/x/s/y")
    assert not rpr.is_reddit_url("file:///etc/passwd")


def test_share_link_resolved_through_the_api_host(fake):
    f = fake(share_api="https://www.reddit.com/r/emacs/comments/1abc2de/a_nice_trick/?share_id=x")
    post = rpr.read_post(SHARE, CID, SECRET, UA)
    assert post.id == "1abc2de" and post.title == "A nice trick"
    urls = [c[1] for c in f.calls]
    assert urls == [rpr.TOKEN_URL, rpr.API + "/r/emacs/s/3Yk3i8s8oO",
                    rpr.API + "/api/info?id=t3_1abc2de&raw_json=1"]
    assert f.calls[1][4] is False                       # the redirect is read, not followed


def test_share_link_falls_back_to_www_without_the_token(fake):
    f = fake(share_api=None, share_www="/r/emacs/comments/1abc2de/a_nice_trick/")
    assert rpr.read_post(SHARE, CID, SECRET, UA).id == "1abc2de"
    www_call = next(c for c in f.calls if c[1].startswith(rpr.WWW + "/r/"))
    assert "Authorization" not in www_call[2] and www_call[2]["User-Agent"] == UA


def test_unresolvable_share_link_says_what_to_do(fake):
    fake(share_api=None, share_www=None)
    with pytest.raises(rpr.RedditError, match=r"oauth.reddit.com: HTTP 403.*full link"):
        rpr.read_post(SHARE, CID, SECRET, UA)


def test_only_the_post_is_requested_never_comments(fake):
    f = fake()
    rpr.read_post("https://www.reddit.com/r/emacs/comments/1abc2de/x/", CID, SECRET, UA)
    assert [c[1] for c in f.calls] == [rpr.TOKEN_URL, rpr.API + "/api/info?id=t3_1abc2de&raw_json=1"]
    assert all("/comments" not in c[1] for c in f.calls[1:])


def test_token_uses_client_credentials_and_errors_never_leak_them(fake):
    f = fake(token_status=401)
    with pytest.raises(rpr.RedditError) as e:
        rpr.read_post(SHARE, CID, SECRET, UA)
    method, url, headers, data, _ = f.calls[0]
    assert data == b"grant_type=client_credentials" and headers["Authorization"].startswith("Basic ")
    assert SECRET not in str(e.value) and CID not in str(e.value) and "HTTP 401" in str(e.value)


def test_missing_post(fake):
    fake(post=None)
    with pytest.raises(rpr.RedditError, match="not found or not public"):
        rpr.read_post("https://redd.it/1abc2de", CID, SECRET, UA)


def test_not_reddit_or_not_a_post(fake):
    f = fake()
    with pytest.raises(rpr.RedditError, match="not a reddit"):
        rpr.read_post("https://example.org/r/x/s/y", CID, SECRET, UA)
    with pytest.raises(rpr.RedditError, match="not a link to a post"):
        rpr.read_post("https://www.reddit.com/r/emacs/", CID, SECRET, UA)
    assert all(c[1] == rpr.TOKEN_URL for c in f.calls)


def test_markdown_for_text_and_link_posts():
    post = rpr.Post("1abc2de", "emacs", "A nice trick", "someone", 1790000000,
                    "https://www.reddit.com/r/emacs/comments/1abc2de/a_nice_trick/", "Body text.", None, False)
    md = rpr.to_markdown(post)
    assert md.startswith("# A nice trick\n\nr/emacs · u/someone · 2026-09-21\nhttps://www.reddit.com/r/emacs/")
    assert md.endswith("Body text.\n") and "Link:" not in md
    link = rpr.to_markdown(rpr.Post(**{**post.__dict__, "text": "", "link": "https://example.org/a"}))
    assert "Link: https://example.org/a" in link
    removed = rpr.to_markdown(rpr.Post(**{**post.__dict__, "text": "[removed]"}))
    assert "(The post's text was removed.)" in removed


def test_cli_with_a_credentials_file(fake, tmp_path, capsys):
    fake(share_api="/r/emacs/comments/1abc2de/")
    creds = tmp_path / "reddit.json"
    creds.write_text(json.dumps({"client_id": CID, "client_secret": SECRET}))
    assert rpr.main([SHARE, "--user-agent", UA, "--credentials", str(creds), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "A nice trick"


def test_cli_errors_are_short_and_secret_free(fake, tmp_path, capsys):
    fake(token_status=401)
    creds = tmp_path / "reddit.json"
    creds.write_text(json.dumps({"client_id": CID, "client_secret": SECRET}))
    assert rpr.main([SHARE, "--user-agent", UA, "--credentials", str(creds)]) == 1
    err = capsys.readouterr().err
    assert err == "reddit-post-reader: token request refused: HTTP 401\n"
