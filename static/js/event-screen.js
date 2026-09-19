"use strict";
// Event screens (templates/partials/event_screen.html) arrive open, so they show and close without JavaScript. Here they
// become modal one at a time: the page behind cannot be reached, Escape closes, and so does a click beside the text.
(() => {
  const screens = [...document.querySelectorAll("dialog.event-screen[open]")];
  if (!screens.length || typeof HTMLDialogElement !== "function") return;
  screens.forEach((screen) => {
    screen.close();
    screen.addEventListener("click", (event) => {
      if (event.target === screen) screen.close();
    });
  });
  const openNext = () => {
    const next = screens.shift();
    if (!next) return;
    next.addEventListener("close", openNext, { once: true });
    next.showModal();
  };
  openNext();
})();
