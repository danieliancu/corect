"use strict";
const menu = document.getElementById("navigation-menu");
if (menu) {
  const summary = menu.querySelector("summary");
  menu.addEventListener("toggle", () => {
    summary.setAttribute("aria-expanded", String(menu.open));
    summary.setAttribute(
      "aria-label",
      menu.open ? "Închide meniul" : "Deschide meniul",
    );
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.open) {
      menu.open = false;
      summary.focus();
    }
  });
  document.addEventListener("click", (event) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
}
// Result boxes ("Engleza ta, corectată", "Sună mai natural:") collapse with the arrow beside their title. The arrows
// stay hidden without JavaScript, so the boxes are simply open; results swapped in by HTMX get working arrows too.
const revealCollapseToggles = () =>
  document.querySelectorAll("[data-collapse-toggle][hidden]").forEach((toggle) => {
    toggle.hidden = false;
    // The details start closed, so the corrected sentence and the natural version are the first things seen. The
    // markup ships open because without JavaScript the arrow never appears and nothing could reopen them. Only
    // toggles still marked hidden are touched, so a box the reader opened is not closed again by a later swap.
    const body = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!body) return;
    toggle.setAttribute("aria-expanded", "false");
    body.hidden = true;
  });
revealCollapseToggles();
document.addEventListener("htmx:afterSwap", revealCollapseToggles);
document.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-collapse-toggle]");
  const body = toggle && document.getElementById(toggle.getAttribute("aria-controls"));
  if (!body) return;
  const open = toggle.getAttribute("aria-expanded") !== "true";
  toggle.setAttribute("aria-expanded", String(open));
  body.hidden = !open;
});
const header = document.querySelector(".site-header");
if (header) {
  // Sticky panels and scroll targets sit below the sticky header, whatever its height.
  new ResizeObserver(() =>
    document.documentElement.style.setProperty(
      "--header-height",
      `${header.offsetHeight}px`,
    ),
  ).observe(header);
}
