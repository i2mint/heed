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


def test_media_plays_inline_and_downloads_as_attachment(root, clip):
    page = publish_review([clip], title="t", rootdir=root)
    client = _client(root)
    url = f"/pages/{page.id}/media/cut 1.mp4"
    assert client.get(url).headers["content-disposition"].startswith("inline")
    saved = client.get(url, params={"download": 1})
    assert saved.headers["content-disposition"].startswith("attachment")
    assert "cut%201.mp4" in saved.headers["content-disposition"]
    assert saved.content == clip.read_bytes()


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


def test_a_token_moderator_is_not_fooled_by_a_login_alone(root, clip):
    """#74: a session is ambient on a shared origin; the secret must be sent too."""
    from heed.threads import MODERATOR_TOKEN_HEADER, token_moderator

    page = publish_review([clip], title="x", rootdir=root)
    secret = "correct-horse-battery"

    class SetEmail:
        def __init__(self, app, email):
            self.app, self.email = app, email

        async def __call__(self, scope, receive, send):
            scope.setdefault("state", {})["user_email"] = self.email
            await self.app(scope, receive, send)

    app = make_review_app(
        root, is_moderator=token_moderator(secret, also=email_moderator(["o@x.com"]))
    )
    owner = TestClient(SetEmail(app, "o@x.com"))
    url = f"/threads/{page.id}"
    cid = owner.post(url, json={"author": "a", "body": "b"}).json()["comment"]["id"]
    hdr = {"X-Heed-Moderate": "1"}
    # Logged in as the owner, but without the secret: no powers at all.
    assert owner.get(url).json()["can_moderate"] is False
    assert owner.delete(f"{url}/{cid}", headers=hdr).status_code == 403
    assert owner.post(url, json={"author": "x", "body": "y"}).json()["comment"]["moderator"] is False
    # The wrong secret is no better; the secret without the login is no better either.
    bad = {**hdr, MODERATOR_TOKEN_HEADER: "wrong-wrong-wrong-wrong"}
    assert owner.delete(f"{url}/{cid}", headers=bad).status_code == 403
    good = {**hdr, MODERATOR_TOKEN_HEADER: secret}
    assert TestClient(app).delete(f"{url}/{cid}", headers=good).status_code == 403
    assert owner.get(url, headers=good).json()["can_moderate"] is True
    assert owner.delete(f"{url}/{cid}", headers=good).status_code == 200


def test_a_weak_moderator_token_is_refused():
    import pytest as _pytest

    from heed.threads import token_moderator

    for weak in ("", "short", None, " padded-token-value-1 ", "tökén-with-non-ascii-1"):
        with _pytest.raises(ValueError, match="token"):
            token_moderator(weak)


# ------------------------------------------------------------ gated pages (heed#88)

PW = "open sesame"


def _gated(root, clip, **kw):
    page = publish_review([clip], title="Cut", rootdir=root, **kw)
    return page, _client(root)


def test_an_ungated_page_is_open_and_makes_no_access_record(root, clip):
    page, client = _gated(root, clip)
    assert client.get(f"/pages/{page.id}").status_code == 200
    assert not (root / "access").exists()


def test_a_password_gates_page_media_and_thread_until_unlocked(root, clip):
    page, client = _gated(root, clip, password=PW)
    paths = [
        f"/pages/{page.id}",
        f"/pages/{page.id}/media/{page.media[0].name}",
        f"/threads/{page.id}",
    ]
    assert [client.get(p).status_code for p in paths] == [401, 401, 401]
    assert (
        client.post(
            f"/threads/{page.id}", json={"author": "a", "body": "b"}
        ).status_code
        == 401
    )
    assert (
        client.post(f"/pages/{page.id}/unlock", json={"password": "wrong"}).status_code
        == 401
    )
    assert client.get(paths[0]).status_code == 401
    ok = client.post(f"/pages/{page.id}/unlock", json={"password": PW})
    assert ok.status_code == 204
    assert "httponly" in ok.headers["set-cookie"].lower()
    assert [client.get(p).status_code for p in paths] == [200, 200, 200]


def test_an_unlock_does_not_open_another_page(root, clip):
    a, client = _gated(root, clip, password=PW)
    b = publish_review([clip], title="B", rootdir=root, password="another one")
    client.post(f"/pages/{a.id}/unlock", json={"password": PW})
    assert client.get(f"/pages/{a.id}").status_code == 200
    assert client.get(f"/pages/{b.id}").status_code == 401


def test_changing_the_password_ends_open_sessions(root, clip):
    page, client = _gated(root, clip, password=PW)
    client.post(f"/pages/{page.id}/unlock", json={"password": PW})
    publish_review([], rootdir=root, to=page.id, password="a new password")
    assert client.get(f"/pages/{page.id}").status_code == 401
    ok = client.post(f"/pages/{page.id}/unlock", json={"password": "a new password"})
    assert ok.status_code == 204


def test_expiry_answers_410_and_revoke_404(root, clip):
    from datetime import datetime, timedelta, timezone

    page, client = _gated(root, clip, expires="1d")
    assert (
        client.get(f"/pages/{page.id}").status_code == 200
    )  # expiry alone: no password
    rec_path = root / "access" / f"{page.id}.json"
    import json

    rec = json.loads(rec_path.read_text())
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    rec["expires"] = past.isoformat()
    rec_path.write_text(json.dumps(rec))
    assert client.get(f"/pages/{page.id}").status_code == 410
    assert client.get(f"/threads/{page.id}").status_code == 410
    publish_review([], rootdir=root, to=page.id, expires="3d")  # extend: open again
    assert client.get(f"/pages/{page.id}").status_code == 200
    assert main(["--dir", str(root), "revoke", page.id]) == 0
    assert client.get(f"/pages/{page.id}").status_code == 404
    assert client.get(f"/pages/{page.id}/media/{page.media[0].name}").status_code == 404


def test_revoking_an_ungated_page_closes_it(root, clip):
    page, client = _gated(root, clip)
    assert main(["--dir", str(root), "revoke", page.id]) == 0
    assert client.get(f"/pages/{page.id}").status_code == 404


def test_wrong_passwords_are_limited_per_client(root, clip):
    from heed.review_access import ReviewGate
    from heed.security import InMemoryRateLimiter

    page = publish_review([clip], title="Cut", rootdir=root, password=PW)
    gate = ReviewGate(root, rate_limiter=InMemoryRateLimiter(max_per_window=3))
    client = _client(root, gate=gate)
    url = f"/pages/{page.id}/unlock"
    codes = [client.post(url, json={"password": "nope"}).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    assert client.post(url, json={"password": PW}).status_code == 429  # even the right one


def test_a_page_taking_too_many_wrong_passwords_locks_for_everyone(root, clip):
    from heed.review_access import ReviewGate
    from heed.security import InMemoryRateLimiter

    now = [0.0]
    page = publish_review([clip], title="Cut", rootdir=root, password=PW)
    gate = ReviewGate(
        root,
        rate_limiter=InMemoryRateLimiter(max_per_window=100),
        page_failure_limiter=InMemoryRateLimiter(max_per_window=2),
        lock_s=60,
        clock=lambda: now[0],
    )
    client = _client(root, gate=gate)
    url = f"/pages/{page.id}/unlock"
    wrong = {"password": "nope"}
    assert [client.post(url, json=wrong).status_code for _ in range(3)] == [401] * 3
    assert client.post(url, json={"password": PW}).status_code == 429  # locked
    now[0] = 61.0
    assert client.post(url, json={"password": PW}).status_code == 204  # lock over


def test_a_bad_password_at_publish_leaves_no_open_page(root, clip):
    with pytest.raises(ValueError):
        publish_review([clip], title="Cut", rootdir=root, password="short")
    assert ReviewStores(root).all_pages() == []
    page = publish_review([clip], title="Cut", rootdir=root)
    with pytest.raises(ValueError):
        publish_review([clip], rootdir=root, to=page.id, password="short")


def test_the_unlock_cookie_does_not_carry_the_page_id(root, clip):
    page, client = _gated(root, clip, password=PW)
    ok = client.post(f"/pages/{page.id}/unlock", json={"password": PW})
    assert page.id not in ok.headers["set-cookie"]


def test_revoking_a_page_with_a_damaged_record_still_closes_it(root, clip):
    page, client = _gated(root, clip, password=PW)
    (root / "access" / f"{page.id}.json").write_text("{not json")
    assert client.get(f"/pages/{page.id}").status_code == 404  # closed, not open
    assert main(["--dir", str(root), "revoke", page.id]) == 0
    assert client.get(f"/pages/{page.id}").status_code == 404


def test_a_gated_page_without_caplinks_fails_closed(root, clip, monkeypatch):
    page, client = _gated(root, clip, password=PW)
    monkeypatch.setitem(__import__("sys").modules, "caplinks", None)
    assert client.get(f"/pages/{page.id}").status_code == 503


def test_unlock_rejects_a_malformed_or_oversized_body(root, clip):
    page, client = _gated(root, clip, password=PW)
    url = f"/pages/{page.id}/unlock"
    assert client.post(url, content=b"nope").status_code == 400
    assert client.post(url, json={"password": 5}).status_code == 400
    assert client.post(url, json={"password": "x" * 5000}).status_code == 413


def test_publish_cli_reads_the_password_from_a_file(root, clip, tmp_path, capsys):
    pw = tmp_path / "pw"
    pw.write_text(PW + "\n")
    argv = ["--dir", str(root), "publish", str(clip), "--title", "t"]
    assert main([*argv, "--password-file", str(pw), "--expires", "7d"]) == 0
    page_id = capsys.readouterr().out.strip().lstrip("#")
    client = _client(root)
    assert client.get(f"/pages/{page_id}").status_code == 401
    assert (
        client.post(f"/pages/{page_id}/unlock", json={"password": PW}).status_code
        == 204
    )


def test_a_dangling_or_symlinked_record_closes_the_page_and_revoke_replaces_it(
    root, clip, tmp_path
):
    page, client = _gated(root, clip, password=PW)
    record = root / "access" / f"{page.id}.json"
    record.unlink()
    record.symlink_to(tmp_path / "nowhere.json")  # dangling
    assert client.get(f"/pages/{page.id}").status_code == 404
    assert main(["--dir", str(root), "revoke", page.id]) == 0
    assert (
        not record.is_symlink() and client.get(f"/pages/{page.id}").status_code == 404
    )
