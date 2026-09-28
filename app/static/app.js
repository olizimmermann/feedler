// Keyboard navigation for the feed: j/k move, u/d vote, s save, o open, x hide, ? help
(function () {
  let current = null;

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

  function click(selector) {
    const el = current && current.querySelector(selector);
    if (el) el.click();
  }

  // Mark an item read when its link is opened, without getting in the way of navigation
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

  // Keep focus on a card after HTMX swaps it (e.g. after voting)
  document.addEventListener("htmx:afterSwap", (e) => {
    if (current && !document.body.contains(current)) {
      const replacement = document.getElementById(current.id);
      current = null;
      if (replacement) focus(replacement);
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    const help = document.getElementById("kbd-help");
    switch (e.key) {
      case "j": move(1); break;
      case "k": move(-1); break;
      case "u": click(".vote.up"); break;
      case "d": click(".vote.down"); break;
      case "s": click('[data-action="save"]'); break;
      case "x": {
        const next = current && cards()[cards().findIndex((c) => c.id === current.id) + 1];
        click('[data-action="hide"]');
        if (next) setTimeout(() => focus(next), 50);
        break;
      }
      case "o":
        if (current) {
          const link = current.querySelector(".title a");
          if (link) { window.open(link.href, "_blank", "noopener"); markRead(link); }
        }
        break;
      case "?": if (help) help.hidden = !help.hidden; break;
      case "Escape": if (help) help.hidden = true; break;
      default: return;
    }
    e.preventDefault();
  });
})();
