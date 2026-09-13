"use strict";
(() => {
  const form = document.getElementById("assistant-form");
  if (!form) return;
  const text = document.getElementById("text");
  const buttons = [...form.querySelectorAll("button[type=submit]")];
  const status = document.getElementById("request-status");
  const result = document.getElementById("result");
  let busy = false;
  const labels = buttons.map(
    (button) => button.querySelector(".button-label").textContent,
  );
  const count = () =>
    form
      .querySelectorAll("[data-count]")
      .forEach(
        (el) => (el.textContent = String(Array.from(text.value).length)),
      );
  text.addEventListener("input", count);
  count();  function measureResult() {
    result.parentElement.style.setProperty(
      "--result-height",
      `${result.getBoundingClientRect().height}px`,
    );
  }
  // Reserve scroll room below a short mobile result, without stretching its card.
  const resultObserver = new ResizeObserver(measureResult);
  resultObserver.observe(result);
  function showLoading(active) {
    const correcting = active.classList.contains("correct-button");
    result.classList.toggle("is-correction-active", correcting);
    const loading = document.createElement("div");
    loading.className = "result-loading";
    const heading = document.createElement("h2");
    heading.textContent = correcting ? "Correction" : "Translation";
    const spinner = document.createElement("span");
    spinner.className = "result-spinner";
    spinner.setAttribute("aria-hidden", "true");
    const message = document.createElement("p");
    message.textContent = correcting
      ? "Verificăm textul tău…"
      : "Traducem textul tău…";
    message.lang = "ro";
    loading.append(heading, spinner, message);
    result.replaceChildren(loading);
    if (correcting) {
      measureResult();
      result.focus({ preventScroll: true });
      requestAnimationFrame(() =>
        result.scrollIntoView({
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)")
            .matches
            ? "instant"
            : "smooth",
          block: "start",
        }),
      );
    }
  }
  function showConnectionError(message) {
    const alert = document.createElement("p");
    alert.className = "error-box";
    alert.setAttribute("role", "alert");
    alert.textContent = message;
    result.replaceChildren(alert);
  }
  function restore() {
    busy = false;
    buttons.forEach((button, i) => {
      button.disabled = false;
      button.querySelector(".button-label").textContent = labels[i];
    });
    result.setAttribute("aria-busy", "false");
    status.textContent = "";
  }
  document.body.addEventListener("htmx:beforeRequest", (event) => {
    if (!form.contains(event.detail.elt)) return;
    if (busy) {
      event.preventDefault();
      return;
    }
    busy = true;
    const active = event.detail.elt;
    buttons.forEach((button) => (button.disabled = true));
    if (active.dataset.loading)
      active.querySelector(".button-label").textContent =
        active.dataset.loading;
    status.textContent = active.dataset.loading || "Working…";
    result.setAttribute("aria-busy", "true");
    const actions = document.getElementById("result-actions");
    if (actions) actions.hidden = true;
    showLoading(active);
  });
  document.body.addEventListener("htmx:beforeSwap", (event) => {
    if (event.detail.target !== result) return;
    if ([400, 409, 422, 429, 503].includes(event.detail.xhr.status)) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });
  document.body.addEventListener("htmx:afterRequest", (event) => {
    if (form.contains(event.detail.elt)) {
      restore();
      if (!event.detail.successful && !result.querySelector('[role="alert"]'))
        showConnectionError("Something went wrong. Please try again.");
    }
  });
  ["htmx:sendError", "htmx:timeout"].forEach((name) =>
    document.body.addEventListener(name, (event) => {
      if (form.contains(event.detail.elt)) {
        restore();
        status.textContent =
          "Connection interrupted. Check your connection and try again.";
        showConnectionError(status.textContent);
      }
    }),
  );
  // Native form fallback also prevents rapid repeat submissions without losing formaction.
  form.addEventListener("submit", (event) => {
    if (window.htmx) return;
    if (busy) {
      event.preventDefault();
      return;
    }
    busy = true;
    status.textContent = event.submitter?.dataset.loading || "Working…";
    setTimeout(() => buttons.forEach((button) => (button.disabled = true)), 0);
  });
  window.addEventListener("pageshow", restore);
})();
