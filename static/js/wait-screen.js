"use strict";
// One waiting screen for the whole site (.wait-screen in app.css, the same one the microphone shows while it starts).
// A form with data-wait="message" shows it on submit, until the next page loads: starting practice, for example, waits
// several seconds while the server prepares the exercises. Delegated, so forms swapped in by HTMX work too.
(() => {
  let screen;

  const show = (message) => {
    if (!screen) {
      screen = document.createElement("div");
      screen.className = "wait-screen";
      screen.setAttribute("role", "status");
      screen.innerHTML = '<span class="wait-screen-spinner" aria-hidden="true"></span><p></p>';
      document.body.append(screen);
    }
    screen.querySelector("p").textContent = message;
    screen.hidden = false;
  };

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-wait]");
    if (!form || event.defaultPrevented) return;
    if (form.dataset.submitting) {
      event.preventDefault(); // One request at a time: a second press would start (and pay for) another one.
      return;
    }
    form.dataset.submitting = "1";
    show(form.dataset.wait);
  });

  // Coming back with the Back button restores this page from the back/forward cache: show it ready to use again.
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    if (screen) screen.hidden = true;
    document.querySelectorAll("form[data-wait][data-submitting]").forEach((form) => delete form.dataset.submitting);
  });
})();
