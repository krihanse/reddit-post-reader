"""Read one public Reddit post (never its comments) through Reddit's API.

    reddit-post-reader URL --user-agent UA [--credentials FILE] [--json]

Authenticates with application-only OAuth (client credentials of a "script"
app): read-only access to public content, no Reddit account password. For
each URL it makes at most four requests: one token, up to two to resolve a
share link (/r/<sub>/s/<code>), and one for the post itself (/api/info).
Standard library only.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

__version__ = "0.1.0"

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"
WWW = "https://www.reddit.com"
TIMEOUT = 20
MAX_BYTES = 2_000_000
HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com",
         "m.reddit.com", "sh.reddit.com", "redd.it"}
POST_ID = re.compile(r"[a-z0-9]{1,12}")
COMMENTS = re.compile(r"/comments/([A-Za-z0-9]{1,12})(?:/|$)")
SHARE = re.compile(r"/r/[A-Za-z0-9_]{1,50}/s/[A-Za-z0-9]{1,30}/?")


class RedditError(Exception):
    """Anything that stops us from reading the post. Messages never contain
    credentials or tokens."""


@dataclass
class Post:
    id: str
    subreddit: str
    title: str
    author: str
    created_utc: float
    permalink: str
    text: str                 # the post's own text (Markdown); "" for a link post
    link: str | None          # where a link post points; None for a text post
    over_18: bool


# --- HTTP ---------------------------------------------------------------------------

@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None   # urllib then raises HTTPError with the 3xx status and Location


def _request(method: str, url: str, headers: dict, data: bytes | None = None,
             follow: bool = True) -> Response:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            return Response(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(MAX_BYTES))
    except urllib.error.HTTPError as e:
        body = e.read(MAX_BYTES) if e.fp else b""
        return Response(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RedditError(f"{method} {urllib.parse.urlsplit(url).netloc}: {e}") from None


# --- URLs ---------------------------------------------------------------------------

def is_reddit_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme in ("http", "https") and (parts.hostname or "").lower() in HOSTS


def post_id(url: str) -> str | None:
    """The post ID in a /comments/<id>/ or redd.it/<id> URL, else None."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "redd.it":
        seg = parts.path.strip("/").lower()
        return seg if POST_ID.fullmatch(seg) else None
    m = COMMENTS.search(parts.path)
    return m.group(1).lower() if m else None


def share_path(url: str) -> str | None:
    """The path of a share link (/r/<sub>/s/<code>), else None."""
    path = urllib.parse.urlsplit(url).path
    return path.rstrip("/") if SHARE.fullmatch(path) else None


# --- API ----------------------------------------------------------------------------

def get_token(client_id: str, client_secret: str, user_agent: str) -> str:
    """An application-only bearer token (read-only, public content)."""
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = _request("POST", TOKEN_URL, {"Authorization": f"Basic {auth}", "User-Agent": user_agent,
                                     "Content-Type": "application/x-www-form-urlencoded"},
                 data=b"grant_type=client_credentials")
    if r.status != 200:
        raise RedditError(f"token request refused: HTTP {r.status}")
    try:
        token = json.loads(r.body).get("access_token")
    except (json.JSONDecodeError, AttributeError):
        token = None
    if not isinstance(token, str) or not token:
        raise RedditError("token response had no access_token")
    return token


def resolve_share(url: str, token: str, user_agent: str) -> str:
    """The post ID behind a share link, from its redirect. Tries the API host
    first, then www.reddit.com (without the token)."""
    path = share_path(url)
    if path is None:
        raise RedditError("not a share link")
    attempts = ((API, {"Authorization": f"bearer {token}", "User-Agent": user_agent}),
                (WWW, {"User-Agent": user_agent}))
    seen = []
    for base, headers in attempts:
        r = _request("GET", base + path, headers, follow=False)
        seen.append(f"{urllib.parse.urlsplit(base).netloc}: HTTP {r.status}")
        location = r.headers.get("location")
        if 300 <= r.status < 400 and location:
            pid = post_id(urllib.parse.urljoin(base, location))
            if pid:
                return pid
    raise RedditError("could not resolve the share link (" + "; ".join(seen) + "); "
                      "use the post's full link (…/comments/<id>/…) instead")


def get_post(pid: str, token: str, user_agent: str) -> Post:
    """One post by ID, through /api/info (no comments)."""
    if not POST_ID.fullmatch(pid):
        raise RedditError(f"not a post ID: {pid!r}")
    r = _request("GET", f"{API}/api/info?id=t3_{pid}&raw_json=1",
                 {"Authorization": f"bearer {token}", "User-Agent": user_agent})
    if r.status != 200:
        raise RedditError(f"post request refused: HTTP {r.status}")
    try:
        children = json.loads(r.body)["data"]["children"]
        d = children[0]["data"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise RedditError(f"post {pid} not found or not public") from None
    is_self = bool(d.get("is_self"))
    return Post(
        id=pid,
        subreddit=str(d.get("subreddit") or ""),
        title=str(d.get("title") or ""),
        author=str(d.get("author") or "[deleted]"),
        created_utc=float(d.get("created_utc") or 0),
        permalink=WWW + str(d.get("permalink") or f"/comments/{pid}/"),
        text=str(d.get("selftext") or "") if is_self else "",
        link=None if is_self else (str(d.get("url")) if d.get("url") else None),
        over_18=bool(d.get("over_18")),
    )


def read_post(url: str, client_id: str, client_secret: str, user_agent: str) -> Post:
    """The post a Reddit URL points to: a post link, redd.it link or share link."""
    if not is_reddit_url(url):
        raise RedditError("not a reddit.com or redd.it URL")
    token = get_token(client_id, client_secret, user_agent)
    pid = post_id(url) or (resolve_share(url, token, user_agent) if share_path(url) else None)
    if pid is None:
        raise RedditError("not a link to a post")
    return get_post(pid, token, user_agent)


# --- output -------------------------------------------------------------------------

def to_markdown(post: Post) -> str:
    date = datetime.fromtimestamp(post.created_utc, tz=timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# {post.title}", "", f"r/{post.subreddit} · u/{post.author} · {date}", post.permalink, ""]
    if post.link:
        lines += [f"Link: {post.link}", ""]
    if post.text in ("[removed]", "[deleted]"):
        lines.append(f"(The post's text was {post.text.strip('[]')}.)")
    elif post.text:
        lines.append(post.text)
    return "\n".join(lines).rstrip() + "\n"


def load_credentials(path: str | None) -> tuple[str, str]:
    """From a JSON file {"client_id": …, "client_secret": …}, else from
    REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET."""
    if path:
        try:
            data = json.loads(open(path, encoding="utf-8").read())
            return str(data["client_id"]), str(data["client_secret"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            raise RedditError(f"could not read credentials from {path}: {type(e).__name__}") from None
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        raise RedditError("no credentials: use --credentials FILE or REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET")
    return cid, secret


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reddit-post-reader", description=__doc__.split("\n")[0])
    ap.add_argument("url")
    ap.add_argument("--user-agent", default=os.environ.get("REDDIT_USER_AGENT"),
                    help='required by Reddit, e.g. "linux:my-notes:0.1.0 (by /u/yourname)"')
    ap.add_argument("--credentials", help='JSON file with "client_id" and "client_secret"')
    ap.add_argument("--json", action="store_true", help="print the post as JSON instead of Markdown")
    args = ap.parse_args(argv)
    if not args.user_agent:
        ap.error("--user-agent (or REDDIT_USER_AGENT) is required")
    try:
        cid, secret = load_credentials(args.credentials)
        post = read_post(args.url, cid, secret, args.user_agent)
    except RedditError as e:
        print(f"reddit-post-reader: {e}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(post), indent=2, ensure_ascii=False) if args.json else to_markdown(post), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
