// Feedler UI helpers: keyboard navigation, read tracking, reading engagement, reader navigation.
(function () {
  let current = null;
  const reader = () => document.querySelector("article.reader");

  function cards() {
    return Array.from(document.querySelectorAll("article.card[data-item]"));
  }

  function focus(card) {
    if (!card) return;
    if (current) current.classList.remove("is-focused");
    current = card;
    card.classList.add("is-focused");
    card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function move(delta) {
    const list = cards();
    if (!list.length) return;
    let i = current ? list.findIndex((c) => c.id === current.id) : -1;
    i = Math.max(0, Math.min(list.length - 1, i + delta));
    focus(list[i]);
  }

  // The element keyboard actions apply to: the focused card, or the reader's action bar
  function scope() {
    return current || document.getElementById("reader-actions");
  }

  function click(selector) {
    const root = scope();
    const el = root && root.querySelector(selector);
    if (el) el.click();
  }

  function post(url, data) {
    const token = document.querySelector('meta[name="csrf-token"]');
    const body = new FormData();
    for (const [k, v] of Object.entries(data || {})) body.append(k, v);
    // keepalive lets the request finish while the page is being closed
    fetch(url, {
      method: "POST",
      headers: { "X-CSRF-Token": token ? token.content : "" },
      body,
      keepalive: true,
    }).catch(() => {});
  }

  // Mark an item read when its original is opened, without getting in the way of navigation.
  // Every open is reported: opening the original is a strong sign of interest.
  function markRead(link) {
    const card = link.closest("article.card");
    if (!link.dataset.readUrl) return;
    if (card) card.classList.add("is-read");
    post(link.dataset.readUrl);
  }

  for (const type of ["click", "auxclick"]) {
    document.addEventListener(type, (e) => {
      const link = e.target.closest && e.target.closest("a.item-link");
      if (link) markRead(link);
      else engageLink(e.target.closest && e.target.closest("article.reader a[href]"));
    });
  }

  // Reading engagement in the reader: active time, scroll depth, comments, outbound links.
  // Only runs when the user has "Learn from reading" on (the server renders data-engage-url).
  const engageUrl = reader() && reader().dataset.engageUrl;
  const IDLE_MS = 60000; // no input for this long = not reading anymore
  const FLUSH_MS = 15000;
  let lastInput = Date.now();
  let pendingMs = 0;
  let maxScroll = 0;
  let sentScroll = 0;

  function engage(data) {
    if (engageUrl) post(engageUrl, data);
  }

  function engageLink(link) {
    if (!link) return;
    let url;
    try { url = new URL(link.href, location.href); } catch (_) { return; }
    if (url.origin !== location.origin) engage({ event: "link" });
  }

  function scrollDepth() {
    const el = reader();
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    const seen = window.innerHeight - rect.top;
    return Math.max(0, Math.min(100, Math.round((seen / Math.max(rect.height, 1)) * 100)));
  }

  function flush() {
    const seconds = Math.floor(pendingMs / 1000);
    if (!seconds && maxScroll <= sentScroll) return;
    pendingMs -= seconds * 1000;
    sentScroll = maxScroll;
    engage({ seconds, scroll: maxScroll });
  }

  if (engageUrl) {
    for (const type of ["scroll", "keydown", "pointerdown", "pointermove", "touchstart", "wheel"]) {
      window.addEventListener(type, () => { lastInput = Date.now(); }, { passive: true });
    }
    window.addEventListener("scroll", () => { maxScroll = Math.max(maxScroll, scrollDepth()); }, { passive: true });
    document.addEventListener("htmx:afterSettle", () => { maxScroll = Math.max(maxScroll, scrollDepth()); });
    maxScroll = scrollDepth();

    let tick = Date.now();
    setInterval(() => {
      const now = Date.now();
      if (document.visibilityState === "visible" && now - lastInput < IDLE_MS) pendingMs += now - tick;
      tick = now;
    }, 1000);
    setInterval(flush, FLUSH_MS);
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
    window.addEventListener("pagehide", flush);

    // Comments count as seen once the loaded list scrolls into view
    let commentsSeen = false;
    document.addEventListener("htmx:afterSettle", () => {
      const list = document.querySelector("#comments ul.comments");
      if (commentsSeen || !list || !("IntersectionObserver" in window)) return;
      commentsSeen = true;
      const seen = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) { engage({ event: "comments" }); seen.disconnect(); }
      });
      seen.observe(list);
    });
  }

  function openOriginal() {
    const link = reader()
      ? document.querySelector(".reader-bar a.item-link")
      : current && current.querySelector("a.item-link");
    if (link) { window.open(link.href, "_blank", "noopener"); markRead(link); }
  }

  // Reader "back": return to the feed at the same scroll position when we came from it
  document.addEventListener("click", (e) => {
    const back = e.target.closest && e.target.closest("a.back-link");
    if (!back) return;
    try {
      const ref = document.referrer ? new URL(document.referrer) : null;
      if (ref && ref.origin === location.origin && ref.pathname === "/" && history.length > 1) {
        e.preventDefault();
        history.back();
      }
    } catch (_) { /* fall through to the link */ }
  });

  // Replacements for inline handlers, which the Content-Security-Policy blocks:
  // data-confirm on a form or submit button asks first, data-busy-text relabels the button
  // that submitted, data-select-on-click selects an input's text.
  document.addEventListener("submit", (e) => {
    const btn = e.submitter;
    const message = (btn && btn.dataset.confirm) || e.target.dataset.confirm;
    if (message && !window.confirm(message)) { e.preventDefault(); return; }
    if (btn && btn.dataset.busyText) btn.textContent = btn.dataset.busyText;
  });

  document.addEventListener("click", (e) => {
    const input = e.target.closest && e.target.closest("input[data-select-on-click]");
    if (input) input.select();
  });

  // Close the phone filter panel when tapping outside it
  document.addEventListener("click", (e) => {
    document.querySelectorAll("details.filter-sheet[open]").forEach((d) => {
      if (!d.contains(e.target)) d.open = false;
    });
  });

  // Keep focus on a card after HTMX swaps it (e.g. after voting)
  document.addEventListener("htmx:afterSwap", () => {
    if (current && !document.body.contains(current)) {
      const replacement = document.getElementById(current.id);
      current = null;
      if (replacement) focus(replacement);
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select", "button", "a", "summary"].includes(tag)) return;
    const help = document.getElementById("kbd-help");
    switch (e.key) {
      case "j": move(1); break;
      case "k": move(-1); break;
      case "u": click(".vote.up"); break;
      case "d": click(".vote.down"); break;
      case "s": click('[data-action="save"]'); break;
      case "o": openOriginal(); break;
      case "n": click('[data-action="next"]'); break;
      case "Enter":
        if (!current) return;
        location.href = current.dataset.reader;
        break;
      case "x": {
        if (!current) return;
        const next = cards()[cards().findIndex((c) => c.id === current.id) + 1];
        click('[data-action="hide"]');
        if (next) setTimeout(() => focus(next), 50);
        break;
      }
      case "?": if (help) help.hidden = !help.hidden; break;
      case "Escape":
        if (help && !help.hidden) help.hidden = true;
        else if (reader()) document.querySelector("a.back-link").click();
        break;
      default: return;
    }
    e.preventDefault();
  });
})();
