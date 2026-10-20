// heed review page: shows the media of one review page and its comment thread.
//
// The page id is the URL fragment (#<id>), so it never reaches a server log or a
// Referer. Every piece of visitor text is set with textContent, never as HTML.
// Endpoints (see heed/review.py): GET {api}/pages/{id}, {api}/pages/{id}/media/{name},
// GET|POST {api}/threads/{id}, DELETE {api}/threads/{id}/{comment}.
(function () {
  "use strict";

  const root = document.getElementById("review");
  const api = root.dataset.api || "";
  const pageId = (() => {
    try { return decodeURIComponent(location.hash.replace(/^#/, "")); } catch (e) { return ""; }
  })();
  const REFRESH_MS = 30000;

  // Per-viewer conveniences only (the commenter's name); the thread lives server-side.
  const prefs = {
    get(key) { try { return localStorage.getItem("heed-review:" + key) || ""; } catch (e) { return ""; } },
    set(key, value) { try { localStorage.setItem("heed-review:" + key, value); } catch (e) { /* private mode */ } },
  };

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    });
    children.flat().forEach((c) => c != null && node.append(c));
    return node;
  }

  function fmtTime(ms) {
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? h + ":" : "") + mm + ":" + String(sec).padStart(2, "0");
  }

  function fmtDate(iso) {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }

  // The moderator token is typed in and kept only in this page's memory: storage is
  // shared by every app on the origin, and a script there could read it.
  let moderatorToken = "";

  async function call(method, path, body, headers) {
    const res = await fetch(api + path, {
      method,
      headers: Object.assign(
        body ? { "Content-Type": "application/json" } : {},
        moderatorToken ? { "X-Heed-Moderator-Token": moderatorToken } : {},
        headers || {},
      ),
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
      cache: "no-store",
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) throw new Error((data && data.detail) || res.statusText || "Request failed");
    return data;
  }

  function fail(message) {
    root.replaceChildren(el("h1", { text: "Review" }), el("p", { class: "muted", text: message }));
  }

  if (!/^[A-Za-z0-9_-]{1,64}$/.test(pageId)) {
    fail("This link is incomplete: ask whoever sent it for the full link.");
    return;
  }

  const players = new Map(); // media name -> <video>/<audio>
  let canModerate = false;
  let lastPlayed = null; // the media element the viewer used last

  function mediaElement(page, item) {
    const src = api + "/pages/" + encodeURIComponent(page.id) + "/media/" + encodeURIComponent(item.name);
    let node;
    if (item.kind === "video") node = el("video", { src, controls: "", preload: "metadata", playsinline: "" });
    else if (item.kind === "audio") node = el("audio", { src, controls: "", preload: "metadata" });
    else node = el("img", { src, alt: item.name, loading: "lazy" });
    if (item.kind !== "image") {
      players.set(item.name, node);
      ["play", "seeked", "timeupdate"].forEach((ev) => node.addEventListener(ev, () => { lastPlayed = node; updatePin(); }));
    }
    return el("figure", {}, node, el("figcaption", { text: item.name }));
  }

  function seek(name, ms) {
    const player = players.get(name) || players.values().next().value;
    if (!player) return;
    player.currentTime = ms / 1000;
    player.scrollIntoView({ behavior: "smooth", block: "center" });
    player.play().catch(() => {});
  }

  const list = el("ol", { class: "comments" });
  const count = el("span", { class: "muted" });

  function renderComment(c) {
    const header = el("header", {},
      el("span", { class: "author", text: c.author }),
      c.moderator ? el("span", { class: "badge", text: "owner" }) : null,
      el("time", { class: "muted", datetime: c.created_at, text: fmtDate(c.created_at) }),
      c.at_ms != null ? el("button", { class: "chip", type: "button", title: "Play from here", text: fmtTime(c.at_ms), onclick: () => seek(c.media, c.at_ms) }) : null,
      c.media && players.size > 1 ? el("span", { class: "muted", text: c.media }) : null,
      canModerate ? el("button", { class: "delete", type: "button", text: "Delete", onclick: () => remove(c) }) : null,
    );
    return el("li", { class: "comment", id: "c-" + c.id }, header, el("div", { class: "body", text: c.body }));
  }

  function renderThread(comments) {
    list.replaceChildren(...comments.map(renderComment));
    count.textContent = comments.length ? "(" + comments.length + ")" : "";
    if (!comments.length) list.append(el("li", { class: "muted", text: "No comments yet. Be the first." }));
  }

  async function loadThread() {
    const data = await call("GET", "/threads/" + encodeURIComponent(pageId));
    canModerate = !!data.can_moderate;
    moderate.hidden = canModerate;
    renderThread(data.comments);
  }

  const moderate = el("button", { class: "chip", type: "button", text: "Moderate", hidden: "", onclick: async () => {
    const token = prompt("Moderator token");
    if (!token) return;
    moderatorToken = token.trim();
    try { await loadThread(); if (!canModerate) { moderatorToken = ""; alert("That token was not accepted."); } }
    catch (e) { moderatorToken = ""; alert(e.message); }
  } });

  async function remove(c) {
    if (!confirm("Delete this comment by " + c.author + "?")) return;
    try { await call("DELETE", "/threads/" + encodeURIComponent(pageId) + "/" + encodeURIComponent(c.id), null, { "X-Heed-Moderate": "1" }); await loadThread(); }
    catch (e) { alert(e.message); }
  }

  // --- the form ---------------------------------------------------------------
  const nameInput = el("input", { type: "text", name: "author", maxlength: "60", required: "", autocomplete: "name", value: prefs.get("author") });
  const bodyInput = el("textarea", { name: "body", maxlength: "4000", required: "", placeholder: "What works, what doesn't…" });
  const pinBox = el("input", { type: "checkbox", checked: "" });
  const pinText = el("span", {});
  const pinRow = el("label", { class: "pin", hidden: "" }, pinBox, pinText);
  const honeypot = el("input", { type: "text", name: "website", tabindex: "-1", autocomplete: "off" });
  const submit = el("button", { class: "primary", type: "submit", text: "Add comment" });
  const status = el("span", { class: "status", role: "status" });

  function currentPin() {
    const player = lastPlayed || (players.size === 1 ? players.values().next().value : null);
    if (!player || !(player.currentTime > 0)) return null;
    const name = [...players].find(([, p]) => p === player)[0];
    return { media: name, at_ms: Math.floor(player.currentTime * 1000) };
  }

  function updatePin() {
    const pin = currentPin();
    pinRow.hidden = !pin;
    if (pin) pinText.textContent = "Pin to " + fmtTime(pin.at_ms) + (players.size > 1 ? " in " + pin.media : " in the video");
  }

  const form = el("form", {
    onsubmit: async (ev) => {
      ev.preventDefault();
      const payload = { author: nameInput.value.trim(), body: bodyInput.value.trim(), website: honeypot.value };
      const pin = pinBox.checked ? currentPin() : null;
      if (pin) Object.assign(payload, pin);
      submit.disabled = true;
      status.className = "status";
      status.textContent = "Sending…";
      try {
        await call("POST", "/threads/" + encodeURIComponent(pageId), payload);
        prefs.set("author", payload.author);
        bodyInput.value = "";
        status.textContent = "Added.";
        await loadThread();
      } catch (e) {
        status.className = "status error";
        status.textContent = e.message;
      } finally {
        submit.disabled = false;
      }
    },
  },
    el("label", {}, "Your name", nameInput),
    el("label", {}, "Comment", bodyInput),
    pinRow,
    el("div", { class: "hp", "aria-hidden": "true" }, honeypot),
    el("div", { class: "actions" }, submit, status),
  );

  async function start() {
    let page;
    try { page = await call("GET", "/pages/" + encodeURIComponent(pageId)); }
    catch (e) { fail("This review page does not exist, or the link is wrong."); return; }
    document.title = page.title;
    root.replaceChildren(
      el("h1", { text: page.title }),
      el("p", { class: "muted", text: "Published " + fmtDate(page.updated_at || page.created_at) }),
      page.note ? el("p", { class: "note", text: page.note }) : null,
      el("section", { class: "media" }, page.media.map((m) => mediaElement(page, m))),
      el("h2", {}, "Comments ", count, " ", moderate),
      list,
      form,
    );
    try { await loadThread(); } catch (e) { list.replaceChildren(el("li", { class: "muted", text: e.message })); }
    setInterval(() => { if (!document.hidden && !bodyInput.matches(":focus")) loadThread().catch(() => {}); }, REFRESH_MS);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) loadThread().catch(() => {}); });
  }

  window.addEventListener("hashchange", () => location.reload());
  start();
})();
