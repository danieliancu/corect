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
