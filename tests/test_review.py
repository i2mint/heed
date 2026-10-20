"""Tests for comment threads and review pages (heed.threads, heed.review)."""

import pytest
from fastapi.testclient import TestClient

from heed.review import ReviewStores, main, make_review_app, publish_review
from heed.threads import email_moderator, list_comments


@pytest.fixture
def root(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "cut 1.mp4"
    path.write_bytes(bytes(range(256)) * 40)
    return path


def _client(root, **kwargs):
    return TestClient(make_review_app(root, **kwargs))


def test_publish_serve_and_comment_roundtrip(root, clip):
    page = publish_review([clip], title="Plot car", note="First cut", rootdir=root)
    client = _client(root)
    got = client.get(f"/pages/{page.id}")
    assert got.status_code == 200
    assert got.json()["title"] == "Plot car"
    assert got.headers["x-robots-tag"].startswith("noindex")
    assert got.headers["referrer-policy"] == "no-referrer"

    media = client.get(
        f"/pages/{page.id}/media/cut 1.mp4", headers={"Range": "bytes=0-9"}
    )
    assert media.status_code == 206
    assert media.content == bytes(range(10))

    posted = client.post(
        f"/threads/{page.id}",
        json={
            "author": "Noel",
            "body": "Slow down here",
            "at_ms": 4200,
            "media": "cut 1.mp4",
        },
    )
    assert posted.status_code == 201
    thread = client.get(f"/threads/{page.id}").json()
    assert [(c["author"], c["at_ms"]) for c in thread["comments"]] == [("Noel", 4200)]
    assert thread["can_moderate"] is False
    # Persisted: a fresh app over the same root sees it.
    assert len(_client(root).get(f"/threads/{page.id}").json()["comments"]) == 1


def test_unknown_page_is_404_everywhere(root, clip):
    publish_review([clip], title="x", rootdir=root)
    client = _client(root)
    bogus = "A" * 22
    assert client.get(f"/pages/{bogus}").status_code == 404
    assert client.get(f"/threads/{bogus}").status_code == 404
    assert (
        client.post(f"/threads/{bogus}", json={"author": "a", "body": "b"}).status_code
        == 404
    )
    assert client.get(f"/pages/{bogus}/media/cut 1.mp4").status_code == 404
    assert client.get("/pages").status_code in (404, 405)  # no HTTP listing at all


def test_media_not_listed_on_the_page_is_not_served(root, clip):
    page = publish_review([clip], title="x", rootdir=root)
    (root / "media" / page.id / "secret.mp4").write_bytes(b"x")
    client = _client(root)
    assert client.get(f"/pages/{page.id}/media/secret.mp4").status_code == 404
    assert (
        client.get(
            f"/pages/{page.id}/media/..%2F..%2Fpages%2F{page.id}.json"
        ).status_code
        == 404
    )


def test_validation_caps_and_honeypot(root, clip):
    page = publish_review([clip], title="x", rootdir=root)
    client = _client(root, rate_limit_per_minute=100, max_comments_per_thread=2)
    url = f"/threads/{page.id}"
    assert client.post(url, json={"author": " ", "body": "b"}).status_code == 422
    assert client.post(url, json={"author": "a", "body": "x" * 5000}).status_code in (
        413,
        422,
    )
    assert (
        client.post(
            url, content=b"[1]", headers={"content-type": "application/json"}
        ).status_code
        == 400
    )
    assert (
        client.post(url, json={"author": "a", "body": "b", "media": "../x"}).status_code
        == 422
    )
    deep = b"[" * 15000
    headers = {"content-type": "application/json"}
    assert client.post(url, content=deep, headers=headers).status_code == 400
    # The honeypot is accepted silently and stores nothing.
    assert (
        client.post(
            url, json={"author": "bot", "body": "spam", "website": "x"}
        ).status_code
        == 201
    )
    assert client.get(url).json()["comments"] == []
    for i in range(2):
        assert client.post(url, json={"author": "a", "body": str(i)}).status_code == 201
    assert client.post(url, json={"author": "a", "body": "full"}).status_code == 409


def test_rate_limit(root, clip):
    page = publish_review([clip], title="x", rootdir=root)
    client = _client(root, rate_limit_per_minute=2)
    codes = [
        client.post(
            f"/threads/{page.id}", json={"author": "a", "body": "b"}
        ).status_code
        for _ in range(3)
    ]
    assert codes == [201, 201, 429]


def test_moderation_needs_the_moderator(root, clip):
    page = publish_review([clip], title="x", rootdir=root)

    class SetEmail:
        def __init__(self, app, email):
            self.app, self.email = app, email

        async def __call__(self, scope, receive, send):
            scope.setdefault("state", {})["user_email"] = self.email
            await self.app(scope, receive, send)

    app = make_review_app(root, is_moderator=email_moderator(["owner@x.com"]))
    anon = TestClient(app)
    cid = anon.post(f"/threads/{page.id}", json={"author": "a", "body": "b"}).json()[
        "comment"
    ]["id"]
    hdr = {"X-Heed-Moderate": "1"}
    url = f"/threads/{page.id}"
    assert anon.delete(f"{url}/{cid}", headers=hdr).status_code == 403
    stranger = TestClient(SetEmail(app, "who@x.com"))
    assert stranger.delete(f"{url}/{cid}", headers=hdr).status_code == 403
    owner = TestClient(SetEmail(app, "Owner@x.com"))
    assert owner.get(url).json()["can_moderate"] is True
    # Without the header (a plain cross-site form could send that), no delete.
    assert owner.delete(f"{url}/{cid}").status_code == 403
    assert owner.delete(f"{url}/{cid}", headers=hdr).status_code == 200
    assert list_comments(ReviewStores(root).threads(page.id)) == []
    by_owner = owner.post(url, json={"author": "Thor", "body": "x"}).json()
    assert by_owner["comment"]["moderator"] is True
    by_anon = anon.post(url, json={"author": "Thor", "body": "y"}).json()
    assert by_anon["comment"]["moderator"] is False


def test_republish_to_same_page_keeps_link_and_thread(root, clip, tmp_path):
    page = publish_review([clip], title="Cut", rootdir=root)
    _client(root).post(f"/threads/{page.id}", json={"author": "a", "body": "b"})
    cut2 = tmp_path / "cut2.mp4"
    cut2.write_bytes(b"2")
    again = publish_review([cut2], rootdir=root, to=page.id)
    assert again.id == page.id
    assert [m.name for m in again.media] == ["cut 1.mp4", "cut2.mp4"]
    assert len(ReviewStores(root).comments_of(page.id)) == 1


def test_refuses_non_media(root, tmp_path):
    html = tmp_path / "x.html"
    html.write_text("<script>")
    with pytest.raises(ValueError):
        publish_review([html], title="x", rootdir=root)


def test_ui_assets_and_shell(root):
    client = _client(root)
    assert "review.js" in client.get("/").text
    assert (
        client.get("/ui/review.js")
        .headers["content-type"]
        .startswith("text/javascript")
    )
    assert client.get("/ui/other.js").status_code == 404


def test_cli_publish_and_comments(root, clip, capsys):
    assert (
        main(
            [
                "--dir",
                str(root),
                "publish",
                str(clip),
                "--title",
                "T",
                "--base-url",
                "https://h/review",
            ]
        )
        == 0
    )
    url = capsys.readouterr().out.strip()
    assert url.startswith("https://h/review/#")
    page_id = url.split("#")[1]
    _client(root).post(
        f"/threads/{page_id}", json={"author": "Noel", "body": "ok", "at_ms": 61000}
    )
    assert main(["--dir", str(root), "comments", page_id]) == 0
    out = capsys.readouterr().out
    assert "Noel @1:01" in out and "ok" in out


def test_per_thread_rate_limit_and_isolated_threads(root, clip):
    from fastapi import FastAPI

    from heed.security import InMemoryRateLimiter
    from heed.threads import make_thread_router

    a = publish_review([clip], title="a", rootdir=root)
    b = publish_review([clip], title="b", rootdir=root)
    stores = ReviewStores(root)
    app = FastAPI()
    app.include_router(
        make_thread_router(
            stores.threads,
            thread_rate_limiter=InMemoryRateLimiter(max_per_window=1),
            rate_limit_per_minute=100,
        )
    )
    client = TestClient(app)

    def post(page, body):
        return client.post(f"/threads/{page.id}", json={"author": "x", "body": body})

    assert post(a, "1").status_code == 201
    assert post(a, "2").status_code == 429
    assert post(b, "3").status_code == 201
    assert [c.body for c in stores.comments_of(a.id)] == ["1"]
    assert [c.body for c in stores.comments_of(b.id)] == ["3"]
