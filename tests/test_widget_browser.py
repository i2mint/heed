"""The built widget in a real (headless) browser, against a real heed backend.

The page and the backend run on different origins, the page carries a strict
Content-Security-Policy (no inline style, Trusted Types required), and the report goes
through the hardened ingest route (origin allow-list, CORS, honeypot, rate limit).
Skipped when Playwright or its Chromium is not installed.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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


class _Store(dict):
    """The sink's reports, with the stored screenshots beside them."""

    def __init__(self):
        super().__init__()
        self.attachments: dict = {}


@contextmanager
def _backend(*, router_kwargs=None, **config):
    """A heed app on its own port; yields (base_url, the sink's store)."""
    store = _Store()
    port = _free_port()
    app = make_app(
        StoreSink(store),
        config=HeedConfig(**config),
        attachment_store=store.attachments,
        **(router_kwargs or {}),
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


NONCE = "t3st-n0nce"


def _page(
    backend: str,
    body: str,
    *,
    csp_extra: str = "",
    script_src: str = "",
    html_attrs: str = "",
) -> str:
    """An embedding page under a strict CSP: no inline style, Trusted Types required,
    scripts only from the backend (or with the test nonce)."""
    csp = (
        f"default-src 'none'; script-src {backend} 'nonce-{NONCE}' {script_src}; "
        f"connect-src {backend}; style-src 'none'; require-trusted-types-for 'script'"
        f"{csp_extra}"
    )
    return f"""<!doctype html>
<html{html_attrs}><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<title>embedder</title></head>
<body><h1>Some site</h1>
{body}
</body></html>"""


def _embed_page(backend: str) -> str:
    """The bundle plus a hand-written element (no loader). Screenshots are off: this
    page's CSP is stricter than the rasterizer can work under (see the screenshot
    tests below)."""
    bundle = load_widget_manifest()["entries"]["widget"]
    return _page(
        backend,
        f'<script src="{backend}/heed/widget/{bundle}"></script>\n'
        f'<heed-feedback endpoint="{backend}/heed/report" screenshot="false">'
        "</heed-feedback>",
    )


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
    page.heed_warnings = warnings = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.on(
        "console", lambda m: warnings.append(m.text) if m.type == "warning" else None
    )
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
def _site(
    browser,
    *,
    page=_embed_page,
    allow_page=True,
    router_kwargs=None,
    static_dir=None,
    **config,
):
    """A page on one origin embedding the widget from a backend on another.

    The page server starts first so the backend can allow exactly its origin (or,
    with ``allow_page=False``, only some other site). ``static_dir`` serves its ``.js``
    files beside the page (an app bundle). Yields (page, console problems, the sink's
    store, the page origin).
    """
    holder: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0].lstrip("/")
            asset = static_dir / path if static_dir and path.endswith(".js") else None
            if asset is not None and asset.is_file():
                body, kind = asset.read_bytes(), "text/javascript"
            else:
                body, kind = holder["html"].encode(), "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", kind)
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
            holder["html"] = page(backend)
            with _open_page(browser, origin + "/") as (tab, problems):
                yield tab, problems, store, origin
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


def test_focus_and_modality_survive_success_and_reparenting(browser):
    with _site(browser) as (page, problems, _store, _origin):
        _send(page, "focus check")
        page.get_by_text("Your report was sent").wait_for()
        assert page.get_by_role("button", name="Close").evaluate(
            "el => el.getRootNode().activeElement === el"
        )
        page.get_by_role("button", name="Close").click()
        # Move the element while its dialog is open (as frameworks sometimes do).
        page.locator("heed-feedback").evaluate(
            "el => { el.open(); document.body.prepend(el); el.open(); }"
        )
        assert page.locator("heed-feedback").evaluate(
            "el => el.shadowRoot.querySelector('dialog').matches(':modal')"
        )
        page.keyboard.press("Escape")
        assert not page.get_by_role("dialog").is_visible()
        assert problems == []


def _loader_page(
    script_attrs: str = "",
    before: str = "",
    *,
    csp_extra: str = "",
    script_src: str = "",
    html_attrs: str = "",
):
    """The documented embed: one async loader tag, optionally after inline calls
    (screenshots off, as in ``_embed_page``)."""
    script_attrs += ' data-screenshot="false"'

    def page(backend: str) -> str:
        return _page(
            backend,
            f"{before}\n"
            f'<script async src="{backend}/heed/widget/heed.js" {script_attrs}>'
            "</script>",
            csp_extra=csp_extra,
            script_src=script_src,
            html_attrs=html_attrs,
        )

    return page


def test_one_script_tag_is_enough(browser):
    with _site(browser, page=_loader_page('data-label="Report a problem"')) as (
        page,
        problems,
        store,
        origin,
    ):
        page.get_by_role("button", name="Report a problem").click()
        page.get_by_label("Title").fill("via the loader")
        page.get_by_role("button", name="Send").click()
        page.get_by_text("Your report was sent").wait_for()
        assert problems == []
        (saved,) = [json.loads(v) for v in store.values()]
        assert (saved["title"], saved["origin"]) == ("via the loader", origin)
        assert page.evaluate("typeof heed.version") == "string"


def test_loader_pinned_with_sri_pins_the_bundle(browser):
    manifest = load_widget_manifest()
    loader = manifest["entries"]["loader"]
    integrity = manifest["files"][loader]["integrity"]

    def page(backend):
        return _page(
            backend,
            f'<script async src="{backend}/heed/widget/{loader}" '
            f'integrity="{integrity}" crossorigin="anonymous"></script>',
        )

    with _site(browser, page=page) as (tab, problems, _store, _origin):
        tab.get_by_role("button", name="Feedback").wait_for()
        bundle_tag = tab.evaluate(
            "() => { const s = document.querySelector('script[src*=heed-widget-]');"
            " return s && [s.integrity, s.crossOrigin]; }"
        )
        widget = manifest["entries"]["widget"]
        assert bundle_tag == [manifest["files"][widget]["integrity"], "anonymous"]
        assert problems == []


def test_calls_queued_before_the_loader_arrives_are_replayed(browser):
    stub = (
        f'<script nonce="{NONCE}">'
        'window.heed=typeof window.heed=="function"?window.heed:'
        "function(){(heed.q=heed.q||[]).push(arguments)};"
        'heed("init", {heading: "Tell us"});'
        'heed("on", "open", function(){ document.title = "opened"; });'
        'heed("open");'
        "</script>"
    )
    page = _loader_page('data-position="none"', before=stub)
    with _site(browser, page=page) as (tab, problems, _store, _origin):
        tab.get_by_role("dialog").wait_for()
        assert tab.get_by_role("heading", name="Tell us").is_visible()
        assert tab.title() == "opened"
        assert not tab.get_by_role("button", name="Feedback").is_visible()
        assert problems == []


def test_data_auto_false_waits_for_init(browser):
    page = _loader_page('data-auto="false"')
    with _site(browser, page=page) as (tab, problems, _store, _origin):
        tab.wait_for_function("window.heed && window.heed.loaded")
        assert tab.locator("heed-feedback").count() == 0
        tab.evaluate("heed.init({label: 'Now'})")
        tab.get_by_role("button", name="Now").wait_for()
        assert problems == []


def test_a_trusted_types_allow_list_without_heed_is_reported_not_crashed(browser):
    def page(backend):
        return _page(
            backend,
            f'<script async src="{backend}/heed/widget/heed.js"></script>',
            csp_extra="; trusted-types app",
        )

    with _site(browser, page=page) as (tab, problems, _store, _origin):
        tab.wait_for_function("window.heed && window.heed.q")
        tab.wait_for_timeout(300)
        assert any("trusted-types" in w for w in tab.heed_warnings)
        assert not any("Failed to set" in p for p in problems), problems


def test_unsubscribing_before_the_bundle_arrives(browser):
    script = (
        f'<script nonce="{NONCE}">'
        "window.calls = 0;"
        "var off = heed.on('open', function(){ window.calls++; }); off();"
        "heed.on('open', function(){ window.calls += 10; });"
        "</script>"
    )

    def page(backend):
        # A synchronous loader, so the inline script runs right after it.
        return _page(
            backend,
            f'<script src="{backend}/heed/widget/heed.js" data-screenshot="false">'
            f"</script>{script}",
        )

    with _site(browser, page=page) as (tab, problems, _store, _origin):
        tab.get_by_role("button", name="Feedback").click()
        tab.get_by_role("dialog").wait_for()
        assert tab.evaluate("window.calls") == 10
        assert problems == []


def test_earlier_builds_stay_served(browser):
    """A loader cached before an upgrade still finds its bundle."""
    manifest = load_widget_manifest()
    old = [
        n
        for n, m in manifest["files"].items()
        if m.get("immutable") and m["version"] != manifest["version"]
    ]
    with _backend() as (backend, _store):
        tab = browser.new_page()
        try:
            for name in old:
                r = tab.request.get(f"{backend}/heed/widget/{name}")
                assert r.status == 200 and r.headers["cache-control"].endswith(
                    "immutable"
                )
        finally:
            tab.close()


def test_an_element_with_id_heed_does_not_stop_the_embed(browser):
    def page(backend):
        return _page(
            backend,
            '<div id="heed">the page\'s own</div>'
            f'<script async src="{backend}/heed/widget/heed.js"></script>',
        )

    with _site(browser, page=page) as (tab, problems, _store, _origin):
        tab.get_by_role("button", name="Feedback").wait_for()
        assert problems == []


def _shot_page(
    backend: str, *, strict: bool = False, extra: str = "", loader_attrs: str = ""
) -> str:
    """A scrolled page with a fixed header, a masked block and a filled-in field.

    Without ``strict`` its CSP is what screenshots need (inline styles allowed, images
    from data: and blob:, no Trusted Types); with it, the strict policy above.
    """
    content = (
        "<style>body{margin:0;font:16px sans-serif}"
        ".hdr{position:fixed;top:0;left:0;right:0;height:40px;background:#00ff00}"
        ".secret{position:absolute;top:900px;left:100px;width:300px;height:100px;"
        "background:#fff;color:#f00;font-size:40px}"
        ".tall{height:3000px;background:#ffffff}</style>"
        '<div class="hdr">header</div>'
        '<div class="secret" data-heed-mask>4111 1111</div>'
        '<input id="email" value="someone@example.com">'
        f"{extra}"
        '<div class="tall"></div>'
        f'<script async src="{backend}/heed/widget/heed.js" {loader_attrs}></script>'
    )
    if strict:
        return _page(backend, content)
    csp = (
        f"default-src 'none'; script-src {backend} 'nonce-{NONCE}'; "
        f"connect-src {backend}; style-src 'unsafe-inline'; img-src data: blob:"
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{csp}"><title>shots</title></head>
<body>{content}</body></html>"""


def _pixel(png: bytes, x: int, y: int):
    from io import BytesIO

    from PIL import Image

    image = Image.open(BytesIO(png)).convert("RGB")
    sx, sy = image.width / 1024, image.height / 700  # the capture scale
    return image.getpixel((int(x * sx), int(y * sy)))


def test_screenshot_of_the_visible_page_with_masking(browser):
    pytest.importorskip("PIL")
    with _site(browser, page=_shot_page) as (page, problems, store, _origin):
        page.evaluate("window.scrollTo(0, 850)")
        page.get_by_role("button", name="Feedback").click()
        preview = page.get_by_role("img", name="Screenshot to be sent")
        preview.wait_for(timeout=15_000)
        page.get_by_label("Title").fill("with a screenshot")
        page.get_by_role("button", name="Send").click()
        page.get_by_text("Your report was sent").wait_for()
        assert problems == []
        (saved,) = [json.loads(v) for v in store.values()]
        (attachment,) = saved["attachments"]
        png = store.attachments[attachment["ref"]]
        assert attachment["media_type"] == "image/png"
        # The scrolled viewport: the fixed header stays at the top...
        assert _pixel(png, 500, 20) == (0, 255, 0)
        # ...the masked block (page y 900-1000, so 50-150 on screen) is black...
        assert _pixel(png, 250, 100) == (0, 0, 0)
        # ...and the page below it is white, not the top of the document, including
        # under the dialog (its own fields are not masked onto the page).
        assert _pixel(png, 800, 400) == (255, 255, 255)
        assert _pixel(png, 512, 350) == (255, 255, 255)


def test_untick_the_screenshot_and_none_is_sent(browser):
    with _site(browser, page=_shot_page) as (page, _problems, store, _origin):
        page.get_by_role("button", name="Feedback").click()
        page.get_by_role("img", name="Screenshot to be sent").wait_for(timeout=15_000)
        page.get_by_label("Include a screenshot of this page").uncheck()
        page.get_by_label("Title").fill("no screenshot please")
        page.get_by_role("button", name="Send").click()
        page.get_by_text("Your report was sent").wait_for()
        (saved,) = [json.loads(v) for v in store.values()]
        assert saved["attachments"] == [] and store.attachments == {}


def test_a_csp_too_strict_for_screenshots_still_sends_the_report(browser):
    def page(backend):
        return _shot_page(backend, strict=True)

    with _site(browser, page=page) as (tab, _problems, store, _origin):
        tab.get_by_role("button", name="Feedback").click()
        tab.get_by_text("No screenshot could be taken").wait_for(timeout=15_000)
        tab.get_by_label("Title").fill("strict page")
        tab.get_by_role("button", name="Send").click()
        tab.get_by_text("Your report was sent").wait_for()
        (saved,) = [json.loads(v) for v in store.values()]
        assert saved["attachments"] == []
        assert any("blocks screenshots" in w for w in tab.heed_warnings)


# Every way a secret has been seen to escape masking; each secret is red text, and the
# stored screenshot must contain no red at all.
_RED_IMG = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' "
    "height='40'%3E%3Crect width='120' height='40' fill='red'/%3E%3C/svg%3E"
)
_LEAKS = (
    "<style>.red{color:#f00;font-size:30px}"
    '.ps::after{content:"SECRET-P";position:absolute;top:30px;color:#f00;'
    "font-size:30px}</style>"
    '<div style="position:absolute;top:1000px;left:20px">'
    '<input id="odd" type="foo" class="red">'
    '<input id="spaced" type="text " class="red">'
    '<input id="mail" class="red" value="someone@example.com">'
    '<textarea class="red">prefilled secret</textarea>'
    '<div data-heed-mask style="height:20px;width:200px;position:relative">'
    '<span class="red" style="position:absolute;top:30px">escaped child</span></div>'
    '<div data-heed-mask class="red" style="height:10px;width:400px">overflowing text '
    "that goes on</div>"
    '<div data-heed-mask style="display:contents"><span class="red">contents</span>'
    "</div>"
    '<div class="pii red">only matched in the live page</div>'
    '<svg width="300" height="40"><text data-heed-mask x="0" y="30" fill="#f00" '
    'font-size="30">svg secret</text></svg>'
    '<div contenteditable class="red">editable</div>'
    '<iframe id="editor" srcdoc="<body style=color:#f00;font-size:30px>x</body>">'
    "</iframe>"
    f'<img data-heed-mask src="{_RED_IMG}">'
    '<div id="sc" style="height:60px;overflow:auto"><div style="height:200px"></div>'
    '<canvas id="cv" data-heed-mask width="200" height="40"></canvas></div>'
    '<div data-heed-mask class="ps" style="position:relative;height:20px;width:100px">'
    "</div>"
    '<div id="cs"><span class="red">SECRET-CS</span></div>'
    "</div>"
    f'<script nonce="{NONCE}">'
    "document.getElementById('odd').value = 'SECRET-A';"
    "document.getElementById('spaced').value = 'SECRET-B';"
    "const f = document.getElementById('editor');"
    "f.addEventListener('load', () => { f.contentDocument.designMode = 'on';"
    " f.contentDocument.body.textContent = 'rich text secret'; });"
    "const c = document.getElementById('cv').getContext('2d');"
    "c.fillStyle = '#f00'; c.fillRect(0, 0, 200, 40);"
    "document.getElementById('sc').scrollTop = 200;"
    "document.getElementById('cs').attachShadow({mode: 'closed'})"
    ".textContent = 'shadow';"
    "</script>"
)


def _red_pixels(png: bytes) -> int:
    from io import BytesIO

    from PIL import Image

    image = Image.open(BytesIO(png)).convert("RGB")
    return sum(
        1 for r, g, b in image.get_flattened_data() if r > 180 and g < 90 and b < 90
    )


def _shot_report(browser, page, scroll_to=950):
    with _site(browser, page=page) as (tab, problems, store, _origin):
        tab.evaluate(f"window.scrollTo(0, {scroll_to})")
        tab.get_by_role("button", name="Feedback").click()
        tab.get_by_role("img", name="Screenshot to be sent").wait_for(timeout=20_000)
        tab.get_by_label("Title").fill("leak hunt")
        tab.get_by_role("button", name="Send").click()
        tab.get_by_text("Your report was sent").wait_for()
        (saved,) = [json.loads(v) for v in store.values()]
        (attachment,) = saved["attachments"]
        return store.attachments[attachment["ref"]], problems


def test_no_secret_reaches_the_screenshot(browser):
    pytest.importorskip("PIL")

    def page(backend):
        return _shot_page(
            backend,
            extra=_LEAKS,
            loader_attrs='data-mask-selector=":root .pii, [data-heed-mask]"',
        )

    png, problems = _shot_report(browser, page)
    assert _red_pixels(png) == 0
    assert problems == []


def test_password_like_fields_are_masked_even_with_mask_inputs_off(browser):
    pytest.importorskip("PIL")
    fields = (
        '<div style="position:absolute;top:1000px;left:20px">'
        '<input class="red" type="text" autocomplete="current-password" '
        'value="shown-password" style="color:#f00;font-size:30px">'
        '<input class="red" autocomplete="cc-number" value="4111111111111111" '
        'style="color:#f00;font-size:30px"></div>'
    )

    def page(backend):
        return _shot_page(
            backend, extra=fields, loader_attrs='data-mask-inputs="false"'
        )

    png, _problems = _shot_report(browser, page)
    assert _red_pixels(png) == 0


def test_masked_images_inside_an_iframe_with_mask_inputs_off(browser):
    pytest.importorskip("PIL")
    inner = f"<img data-heed-mask src=&quot;{_RED_IMG}&quot;>"
    frame = (
        '<div style="position:absolute;top:1000px;left:20px">'
        f'<iframe srcdoc="{inner}" style="width:300px;height:100px"></iframe></div>'
    )

    def page(backend):
        return _shot_page(backend, extra=frame, loader_attrs='data-mask-inputs="false"')

    png, _problems = _shot_report(browser, page)
    assert _red_pixels(png) == 0


WIDGET_DIR = Path(__file__).resolve().parent.parent / "widget"
_REACT_APP = """
import { StrictMode, createElement } from "react";
import { createRoot } from "react-dom/client";
import { HeedFeedback } from "heed-widget/react";

createRoot(document.getElementById("root")).render(
  createElement(StrictMode, null, createElement(HeedFeedback, {
    endpoint: document.body.dataset.endpoint,
    label: "React feedback",
    "data-testid": "feedback",
    onSubmitted: (report) => { document.title = "sent " + report.status; },
  })),
);
"""


@pytest.fixture(scope="module")
def react_app(tmp_path_factory):
    """The shipped ES module package (build-lib.mjs), resolved through its
    ``exports`` map, inside a small React app in StrictMode."""
    esbuild = WIDGET_DIR / "node_modules" / ".bin" / "esbuild"
    node = shutil.which("node")
    if not (node and esbuild.exists()):
        pytest.skip("needs node and widget/node_modules (npm ci in widget/)")
    root = tmp_path_factory.mktemp("react-app")
    package = root / "node_modules" / "heed-widget"
    subprocess.run(
        [node, str(WIDGET_DIR / "build-lib.mjs"), str(package / "dist")],
        check=True,
        capture_output=True,
    )
    shutil.copy(WIDGET_DIR / "package.json", package / "package.json")
    (root / "main.js").write_text(_REACT_APP)
    out = root / "out"
    subprocess.run(
        [
            str(esbuild),
            str(root / "main.js"),
            "--bundle",
            "--format=esm",
            "--splitting",
            f"--outdir={out}",
            "--log-level=error",
        ],
        check=True,
        env={**os.environ, "NODE_PATH": str(WIDGET_DIR / "node_modules")},
    )
    return out


def test_react_wrapper_in_a_bundled_app(browser, react_app):
    pytest.importorskip("PIL")

    def page(backend):
        csp = (
            "default-src 'none'; script-src 'self'; "
            f"connect-src {backend}; style-src 'unsafe-inline'; img-src data: blob:"
        )
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{csp}"><title>react</title></head>
<body data-endpoint="{backend}/heed/report"><div id="root"></div>
<script type="module" src="/main.js"></script></body></html>"""

    with _site(browser, page=page, static_dir=react_app) as (tab, problems, store, _o):
        assert tab.get_by_test_id("feedback").count() == 1  # StrictMode: one element
        tab.get_by_role("button", name="React feedback").click()
        # The rasterizer comes from the app's own lazily loaded chunk.
        tab.get_by_role("img", name="Screenshot to be sent").wait_for(timeout=20_000)
        tab.get_by_label("Title").fill("from a React app")
        tab.get_by_role("button", name="Send").click()
        sync_api.expect(tab).to_have_title("sent received")
        assert problems == []
        (saved,) = [json.loads(v) for v in store.values()]
        assert saved["title"] == "from a React app"
        assert len(saved["attachments"]) == 1


# --- the built-in bot-gate adapter (heed#37) ---------------------------------------

_FAKE_TURNSTILE = """
window.turnstile = {
  log: [],
  render(el, params) {
    this.log.push("render:" + params.sitekey + ":" + params.execution);
    this.params = params;
    el.append(document.createElement("iframe"));
    return "w1";
  },
  execute() {
    this.log.push("execute");
    setTimeout(() => this.params.callback("human"), 5);
  },
  reset() { this.log.push("reset"); },
  remove() { this.log.push("remove"); },
};
"""


def _gate_page(*, csp_extra: str = "", script_src: str = "'self'", init: str):
    """The loader with ``data-bot-gate``, then an inline ``heed.init`` (nonced)."""
    stub = f'<script nonce="{NONCE}">{init}</script>'
    return _loader_page(
        'data-bot-gate="turnstile" data-bot-sitekey="SITE"',
        before=stub,
        csp_extra=csp_extra,
        script_src=script_src,
    )


_FAKE_INIT = (
    "window.heed=window.heed||function(){(heed.q=heed.q||[]).push(arguments)};"
    "heed('init',{botGateScript:'/fake-turnstile.js'});"
)


def test_built_in_turnstile_gate_supplies_the_token(browser, tmp_path):
    (tmp_path / "fake-turnstile.js").write_text(_FAKE_TURNSTILE)
    verifier = _TokenVerifier()
    # Trusted Types are enforced and the allow-list names heed-bot-gate.
    page = _gate_page(init=_FAKE_INIT, csp_extra="; trusted-types heed heed-bot-gate")
    with _site(
        browser,
        page=page,
        static_dir=tmp_path,
        router_kwargs={"bot_verifier": verifier},
    ) as (tab, problems, store, _origin):
        tab.get_by_role("button", name="Feedback").click()
        tab.wait_for_function("window.turnstile && window.turnstile.log.length")
        # The challenge lives inside the (modal) dialog, where it can be clicked.
        assert tab.locator("heed-feedback").evaluate(
            "el => !!el.shadowRoot.querySelector('dialog .gate iframe')"
        )
        tab.get_by_label("Title").fill("gated")
        tab.get_by_role("button", name="Send").click()
        tab.get_by_text("Your report was sent").wait_for()
        assert verifier.seen == ["human"] and len(store) == 1
        assert tab.evaluate("window.turnstile.log") == [
            "render:SITE:execute",
            "execute",
        ]
        assert problems == []


def test_gate_script_blocked_by_trusted_types_fails_closed_and_says_why(
    browser, tmp_path
):
    (tmp_path / "fake-turnstile.js").write_text(_FAKE_TURNSTILE)
    verifier = _TokenVerifier()
    page = _gate_page(init=_FAKE_INIT, csp_extra="; trusted-types heed")
    with _site(
        browser,
        page=page,
        static_dir=tmp_path,
        router_kwargs={"bot_verifier": verifier},
    ) as (tab, _problems, store, _origin):
        tab.get_by_role("button", name="Feedback").click()
        tab.get_by_label("Title").fill("blocked")
        tab.get_by_role("button", name="Send").click()
        tab.get_by_text("could not confirm you are human").wait_for()
        assert store == {} and verifier.seen == []
        assert any("heed-bot-gate" in w for w in tab.heed_warnings)


def _cloudflare_reachable() -> bool:
    try:
        socket.create_connection(("challenges.cloudflare.com", 443), 3).close()
        return True
    except OSError:
        return False


# Cloudflare's documented dummy keys: this one always passes and renders no widget UI;
# the matching token is "XXXX.DUMMY.TOKEN.XXXX".
_CF_ALWAYS_PASSES = "1x00000000000000000000BB"
_CF_DUMMY_TOKEN = "XXXX.DUMMY.TOKEN.XXXX"


@pytest.mark.skipif(
    not _cloudflare_reachable(), reason="needs network access to Cloudflare"
)
def test_real_turnstile_with_cloudflares_test_sitekey(browser):
    class Verifier:
        seen: list = []

        def verify(self, token, *, remote_ip=None):
            self.seen.append(token)
            return token == _CF_DUMMY_TOKEN

    verifier = Verifier()
    cf = "https://challenges.cloudflare.com"
    page = _loader_page(
        f'data-bot-gate="turnstile" data-bot-sitekey="{_CF_ALWAYS_PASSES}"',
        csp_extra=f"; frame-src {cf}; trusted-types heed heed-bot-gate",
        script_src=cf,
    )
    with _site(browser, page=page, router_kwargs={"bot_verifier": verifier}) as (
        tab,
        _problems,
        store,
        _origin,
    ):
        tab.get_by_role("button", name="Feedback").click()
        tab.get_by_label("Title").fill("through the real thing")
        tab.get_by_role("button", name="Send").click()
        tab.get_by_text("Your report was sent").wait_for(timeout=30_000)
        assert verifier.seen == [_CF_DUMMY_TOKEN] and len(store) == 1
        # An invisible challenge leaves no empty box (and no gap) in the form.
        assert tab.locator("heed-feedback .gate").is_hidden()


_CF_FORCED_INTERACTIVE = "3x00000000000000000000FF"


@pytest.mark.skipif(
    not _cloudflare_reachable(), reason="needs network access to Cloudflare"
)
def test_real_interactive_turnstile_challenge_is_clickable_in_the_modal_dialog(
    browser,
):
    """Cloudflare's forced-interactive test key: the visitor must tick the box, and the
    box must be reachable although a modal dialog makes the rest of the page inert."""

    class Verifier:
        seen: list = []

        def verify(self, token, *, remote_ip=None):
            self.seen.append(token)
            return token == _CF_DUMMY_TOKEN

    verifier = Verifier()
    cf = "https://challenges.cloudflare.com"
    page = _loader_page(
        f'data-bot-gate="turnstile" data-bot-sitekey="{_CF_FORCED_INTERACTIVE}"',
        csp_extra=f"; frame-src {cf}; trusted-types heed heed-bot-gate",
        script_src=cf,
    )
    with _site(browser, page=page, router_kwargs={"bot_verifier": verifier}) as (
        tab,
        _problems,
        store,
        _origin,
    ):
        tab.get_by_role("button", name="Feedback").click()
        tab.get_by_label("Title").fill("tick the box")
        tab.get_by_role("button", name="Send").click()
        tab.get_by_text("Please complete the check").wait_for(timeout=30_000)
        gate = tab.locator("heed-feedback .gate")
        gate.wait_for(state="visible", timeout=30_000)  # shown: the challenge needs the visitor
        tab.wait_for_timeout(2_000)  # the checkbox appears after the frame's animation
        box = gate.bounding_box()
        # The checkbox sits in a closed shadow root of a cross-origin frame: click by position.
        tab.mouse.click(box["x"] + 32, box["y"] + 32)
        tab.get_by_text("Your report was sent").wait_for(timeout=30_000)
        assert verifier.seen == [_CF_DUMMY_TOKEN] and len(store) == 1
        assert not gate.is_visible()  # and hidden again once it is done


def _hcaptcha_reachable() -> bool:
    try:
        socket.create_connection(("js.hcaptcha.com", 443), 3).close()
        return True
    except OSError:
        return False


# hCaptcha's documented test sitekey (always passes); its token is the fixed string below.
_HC_TEST_SITEKEY = "10000000-ffff-ffff-ffff-000000000001"
_HC_TEST_TOKEN = "10000000-aaaa-bbbb-cccc-000000000001"


@pytest.mark.skipif(
    not _hcaptcha_reachable(), reason="needs network access to hCaptcha"
)
def test_real_hcaptcha_with_its_test_sitekey(browser):
    class Verifier:
        seen: list = []

        def verify(self, token, *, remote_ip=None):
            self.seen.append(token)
            return token == _HC_TEST_TOKEN

    verifier = Verifier()
    hc = "https://js.hcaptcha.com https://*.hcaptcha.com https://hcaptcha.com"
    page = _loader_page(
        f'data-bot-gate="hcaptcha" data-bot-sitekey="{_HC_TEST_SITEKEY}"',
        csp_extra=f"; frame-src {hc}; connect-src *; trusted-types heed heed-bot-gate",
        script_src=hc,
    )
    with _site(browser, page=page, router_kwargs={"bot_verifier": verifier}) as (
        tab,
        _problems,
        store,
        _origin,
    ):
        tab.get_by_role("button", name="Feedback").click()
        tab.get_by_label("Title").fill("through hcaptcha")
        tab.get_by_role("button", name="Send").click()
        tab.get_by_text("Your report was sent").wait_for(timeout=30_000)
        assert verifier.seen == [_HC_TEST_TOKEN] and len(store) == 1


# --- translations (heed#39) --------------------------------------------------------


def _requests(tab) -> list[str]:
    seen: list[str] = []
    tab.on("request", lambda r: seen.append(r.url))
    return seen


def test_a_french_page_gets_a_french_dialog_under_strict_csp(browser):
    # Strict CSP, Trusted Types enforced, loader pinned to the bundle by SRI: the
    # translations file rides the same chain (bundle -> locales, with its own SRI).
    page = _loader_page(html_attrs=' lang="fr"')
    with _site(browser, page=page) as (tab, problems, store, _origin):
        tab.get_by_role("button", name="Commentaires").click()
        tab.get_by_role("heading", name="Envoyer un commentaire").wait_for()
        tab.get_by_label("Titre").fill("Le bouton ne répond pas")
        tab.get_by_role("button", name="Envoyer").click()
        tab.get_by_text("Votre signalement a été envoyé").wait_for()
        assert len(store) == 1 and problems == []
        tag = tab.evaluate(
            "() => { const s = document.querySelector('script[src*=heed-locales-]');"
            " return s && [s.integrity, s.crossOrigin]; }"
        )
        assert tag and tag[0].startswith("sha384-") and tag[1] == "anonymous"


def test_an_english_page_never_fetches_the_translations(browser):
    page = _loader_page(html_attrs=' lang="en"')
    with _site(browser, page=page) as (tab, problems, _store, _origin):
        seen = _requests(tab)
        tab.get_by_role("button", name="Feedback").click()
        tab.get_by_role("dialog").wait_for()
        tab.wait_for_timeout(300)
        assert not [u for u in seen if "heed-locales" in u]
        assert problems == []


def test_messages_override_single_texts_from_heed_init(browser):
    init = (
        f'<script nonce="{NONCE}">window.heed=window.heed||function()'
        "{(heed.q=heed.q||[]).push(arguments)};"
        'heed("init",{lang:"de",messages:{send:"Abschicken"}});</script>'
    )
    page = _loader_page(before=init)
    with _site(browser, page=page) as (tab, problems, _store, _origin):
        tab.get_by_role("button", name="Feedback").click()
        tab.get_by_role("button", name="Abschicken").wait_for()
        tab.get_by_role("button", name="Abbrechen").wait_for()
        assert problems == []
