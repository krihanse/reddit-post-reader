# reddit-post-reader

A small, read-only tool that fetches **one public Reddit post** (its title,
text and link) through Reddit's official API, so I can keep posts I shared
myself in my personal notes.

## What it does, and what it doesn't

- **One post per request, on demand.** It runs when I save a Reddit link to
  my notes: a few posts a week. No crawling, no listings, no searching, no
  scheduled collection.
- **The post only.** Title, text, link, subreddit, author name and date.
  It never requests comments, votes, user profiles or other users' data.
- **Read-only.** Application-only OAuth with a "script" app's client ID and
  secret: public content only. It never logs in as a user, posts, votes or
  sends messages.
- **Personal and non-commercial.** The text goes into my private notes. It
  is not published, shared, sold, or used to train machine-learning models.
- **Deleted content stays deleted.** A post that was removed or deleted is
  reported as such; its text isn't recovered from anywhere else.
- **Few requests.** At most four per post: one token, up to two to resolve a
  share link (`/r/<sub>/s/<code>`) from its redirect, and one for the post
  (`/api/info`). Far below Reddit's rate limits.
- **Identifies itself.** Every request carries a descriptive User-Agent in
  Reddit's format, `<platform>:<app ID>:<version> (by /u/<username>)`.

## Usage

Python 3.10 or later, standard library only.

```sh
pip install .        # or run reddit_post_reader.py directly
reddit-post-reader https://www.reddit.com/r/<subreddit>/s/<code> \
    --user-agent "linux:my-notes-reader:0.1.0 (by /u/yourname)" \
    --credentials ~/.config/reddit-post-reader.json
```

The credentials file (keep it private, e.g. mode 600):

```json
{"client_id": "…", "client_secret": "…"}
```

or set `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` instead. The
User-Agent can also come from `REDDIT_USER_AGENT`.

Output is Markdown by default, JSON with `--json`. Accepted links: post
links (`/r/<sub>/comments/<id>/…`), `redd.it/<id>`, and share links
(`/r/<sub>/s/<code>`).

As a library:

```python
from reddit_post_reader import read_post, to_markdown

post = read_post(url, client_id, client_secret, user_agent)
print(to_markdown(post))
```

Errors raise `RedditError`; its messages never contain credentials or
tokens.

## Tests

```sh
python -m pytest tests
```

The tests use a fake of Reddit's API and make no network requests.

## License

MIT, see `LICENSE`.
