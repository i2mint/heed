"""The built widget in a real (headless) browser, against a real heed backend.

The page and the backend run on different origins, the page carries a strict
Content-Security-Policy (no inline style, Trusted Types required), and the report goes
through the hardened ingest route (origin allow-list, CORS, honeypot, rate limit).
Skipped when Playwright or its Chromium is not installed.
"""

import json
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from heed import HeedConfig, StoreSink, make_app
from heed.widget import load_widget_manifest

sync_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


@contextmanager
def _backend(*, router_kwargs=None, **config):
    """A heed app on its own port; yields (base_url, the sink's store)."""
    store: dict = {}
    port = _free_port()
    app = make_app(
        StoreSink(store), config=HeedConfig(**config), **(router_kwargs or {})
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("heed test backend did not start")
        time.sleep(0.02)
    try:
        yield f"http://{HOST}:{port}", store
    finally:
        server.should_exit = True
        thread.join(5)


def _embed_page(backend: str, *, extra_attrs: str = "") -> str:
    bundle = load_widget_manifest()["entries"]["widget"]
    csp = (
        f"default-src 'none'; script-src {backend}; connect-src {backend}; "
        "style-src 'none'; require-trusted-types-for 'script'"
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<title>embedder</title></head>
<body><h1>Some site</h1>
<script src="{backend}/heed/widget/{bundle}"></script>
<heed-feedback endpoint="{backend}/heed/report" {extra_attrs}></heed-feedback>
</body></html>"""


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browsers not installed
            pytest.skip(f"no Chromium for Playwright: {e}")
        yield b
        b.close()


@contextmanager
def _open_page(browser, url):
    page = browser.new_page(viewport={"width": 1024, "height": 700})
    problems: list[str] = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.on(
        "console",
        lambda m: (
            problems.append(m.text)
            if m.type == "error" and "Failed to load resource" not in m.text
            else None
        ),
    )
    page.goto(url)
    try:
        yield page, problems
    finally:
        page.close()


@contextmanager
def _site(browser, *, allow_page=True, router_kwargs=None, **config):
    """A page on one origin embedding the widget from a backend on another.

    The page server starts first so the backend can allow exactly its origin (or,
    with ``allow_page=False``, only some other site). Yields (page, console problems,
    the sink's store, the page origin).
    """
    holder: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = holder["html"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer((HOST, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://{HOST}:{server.server_address[1]}"
    allowed = [origin if allow_page else "https://other.example"]
    try:
        with _backend(
            allowed_origins=allowed, router_kwargs=router_kwargs, **config
        ) as (backend, store):
            holder["html"] = _embed_page(backend)
            with _open_page(browser, origin + "/") as (page, problems):
                yield page, problems, store, origin
    finally:
        server.shutdown()


def _send(page, title, body=""):
    page.get_by_role("button", name="Feedback").click()
    page.get_by_label("Title").fill(title)
    page.get_by_label("Details").fill(body)
    page.get_by_role("button", name="Send").click()


def test_report_from_a_cross_origin_page_under_strict_csp(browser):
    with _site(browser) as (page, problems, store, origin):
        _send(page, "Checkout button does nothing", "Clicked twice, no response.")
        page.get_by_text("Your report was sent").wait_for()
        # Constructed stylesheets apply despite style-src 'none'.
        bg = page.locator("heed-feedback").evaluate(
            "el => getComputedStyle(el.shadowRoot.querySelector('.launcher'))"
            ".backgroundColor"
        )
        assert bg == "rgb(37, 99, 235)"
        assert problems == []
        (saved,) = [json.loads(v) for v in store.values()]
        assert saved["title"] == "Checkout button does nothing"
        assert saved["body"] == "Clicked twice, no response."
        assert saved["page_url"] == origin + "/"
        assert saved["env"]["viewport"] == "1024x700"
        assert saved["origin"] == origin


def test_dialog_is_modal_and_escape_keeps_the_draft(browser):
    with _site(browser) as (page, problems, store, _):
        page.get_by_role("button", name="Feedback").click()
        page.get_by_label("Title").fill("half-written")
        assert page.get_by_label("Title").evaluate(
            "el => el.getRootNode().activeElement === el"
        )
        page.keyboard.press("Escape")
        assert not page.get_by_role("dialog").is_visible()
        page.get_by_role("button", name="Feedback").click()
        assert page.get_by_label("Title").input_value() == "half-written"
        assert store == {} and problems == []


def test_a_site_the_backend_does_not_allow_cannot_send(browser):
    with _site(browser, allow_page=False) as (page, _, store, _origin):
        _send(page, "from an unlisted site")
        page.get_by_text("Could not reach the feedback service").wait_for()
        assert store == {}


def test_rate_limited_visitor_is_told_when_to_retry(browser):
    with _site(browser, rate_limit_per_minute=1) as (page, _, store, _origin):
        _send(page, "first")
        page.get_by_text("Your report was sent").wait_for()
        page.get_by_role("button", name="Close").click()
        _send(page, "second")
        page.get_by_text("try again in 60 seconds").wait_for()
        assert page.get_by_label("Title").input_value() == "second"
        assert len(store) == 1


class _TokenVerifier:
    """A stand-in for Turnstile: only the token "human" passes."""

    def __init__(self):
        self.seen = []

    def verify(self, token, *, remote_ip=None):
        self.seen.append(token)
        return token == "human"


def test_bot_gate_token_comes_from_the_embedders_provider(browser):
    verifier = _TokenVerifier()
    kwargs = {"bot_verifier": verifier}
    with _site(browser, router_kwargs=kwargs) as (page, _, store, _origin):
        _send(page, "no token")
        page.get_by_text("could not confirm you are human").wait_for()
        assert store == {}
        page.locator("heed-feedback").evaluate(
            "el => { el.botToken = async () => 'human'; }"
        )
        page.get_by_role("button", name="Send").click()
        page.get_by_text("Your report was sent").wait_for()
        assert verifier.seen == [None, "human"] and len(store) == 1
