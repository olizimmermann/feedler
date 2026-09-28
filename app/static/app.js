// Feedler UI helpers: keyboard navigation, read tracking, reader navigation.
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

  // Mark an item read when its original is opened, without getting in the way of navigation
  function markRead(link) {
    const card = link.closest("article.card");
    if (!link.dataset.readUrl || (card && card.classList.contains("is-read"))) return;
    if (card) card.classList.add("is-read");
    const token = document.querySelector('meta[name="csrf-token"]');
    fetch(link.dataset.readUrl, {
      method: "POST",
      headers: { "X-CSRF-Token": token ? token.content : "" },
      keepalive: true,
    }).catch(() => {});
  }

  for (const type of ["click", "auxclick"]) {
    document.addEventListener(type, (e) => {
      const link = e.target.closest && e.target.closest("a.item-link");
      if (link) markRead(link);
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
