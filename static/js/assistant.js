"use strict";
(() => {
  const form = document.getElementById("assistant-form");
  if (!form) return;
  const text = document.getElementById("text");
  const status = document.getElementById("request-status");
  const result = document.getElementById("result");
  // "Vreau să sune natural!" never changes its look while a request runs; `busy` alone blocks repeat submissions.
  let busy = false;
  // The × at the top right of the text box clears everything. Shown only while there is text and the box can be
  // edited (not during live transcription); out of the tab order, since the keyboard already has Delete.
  const clear = form.querySelector("[data-clear-text]");
  const showClear = () => {
    if (clear) clear.hidden = !text.value || text.readOnly;
  };
  clear?.addEventListener("click", () => {
    if (text.readOnly) return;
    text.value = "";
    text.dispatchEvent(new Event("input", { bubbles: true }));
    text.focus();
  });
  // The microphone changes the box to read-only and back; check once its state change has been applied.
  form.addEventListener("voice-state", () => requestAnimationFrame(showClear));
  const count = () => {
    showClear();
    const length = Array.from(text.value).length;
    form.querySelectorAll("[data-count]").forEach((el) => (el.textContent = String(length)));
    // An empty box shows an invitation to speak or type instead of "0/2000".
    form.querySelectorAll("[data-counter]").forEach((el) => el.classList.toggle("is-empty", length === 0));
  };
  text.addEventListener("input", count);
  count();
  // Links to the editor ("Poți începe și fără cont", "Scrie primul text", "Mergi la editor") scroll to the very top,
  // where the title and the editor are, and put the cursor in the text box.
  document.addEventListener("click", (event) => {
    if (!event.target.closest('a[href="#text"]')) return;
    event.preventDefault();
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
    text.focus({ preventScroll: true });
  });
  function measureResult() {
    result.parentElement.style.setProperty(
      "--result-height",
      `${result.getBoundingClientRect().height}px`,
    );
  }
  // Reserve scroll room below a short mobile result, without stretching its card.
  const resultObserver = new ResizeObserver(measureResult);
  resultObserver.observe(result);
  function showLoading() {
    result.classList.add("is-result-active");
    const loading = document.createElement("div");
    loading.className = "result-loading";
    const heading = document.createElement("h2");
    heading.textContent = "Engleză britanică";
    const spinner = document.createElement("span");
    spinner.className = "result-spinner";
    spinner.setAttribute("aria-hidden", "true");
    const message = document.createElement("p");
    message.textContent = "Conversie în text natural…";
    message.lang = "ro";
    loading.append(heading, spinner, message);
    result.replaceChildren(loading);
    measureResult();
    result.focus({ preventScroll: true });
    requestAnimationFrame(() =>
      result.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
        block: "start",
      }),
    );
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
    status.textContent = event.detail.elt.dataset.loading || "Se lucrează…";
    result.setAttribute("aria-busy", "true");
    const actions = document.getElementById("result-actions");
    if (actions) actions.hidden = true;
    showLoading();
  });
  document.body.addEventListener("htmx:beforeSwap", (event) => {
    if (event.detail.target !== result) return;
    if ([400, 403, 409, 422, 429, 503].includes(event.detail.xhr.status)) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });
  document.body.addEventListener("htmx:afterRequest", (event) => {
    if (form.contains(event.detail.elt)) {
      restore();
      if (!event.detail.successful && !result.querySelector('[role="alert"]'))
        showConnectionError("Ceva nu a mers. Încearcă din nou.");
    }
  });
  ["htmx:sendError", "htmx:timeout"].forEach((name) =>
    document.body.addEventListener(name, (event) => {
      if (form.contains(event.detail.elt)) {
        restore();
        status.textContent =
          "Conexiunea s-a întrerupt. Verifică internetul și încearcă din nou.";
        showConnectionError(status.textContent);
      }
    }),
  );
  // Native form fallback also prevents rapid repeat submissions.
  form.addEventListener("submit", (event) => {
    if (window.htmx) return;
    if (busy) {
      event.preventDefault();
      return;
    }
    busy = true;
    status.textContent = event.submitter?.dataset.loading || "Se lucrează…";
  });
  window.addEventListener("pageshow", restore);
})();
