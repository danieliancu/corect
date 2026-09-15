"use strict";
// The small copy icon at the bottom right of every English result copies that sentence. It sits inside the sentence's
// paragraph, so the text is the paragraph's own text without the button. Delegated, so results swapped in by HTMX work.
(() => {
  let announcer;
  const announce = (message) => {
    if (!announcer) {
      announcer = document.createElement("p");
      announcer.className = "sr-only";
      announcer.setAttribute("role", "status");
      document.body.append(announcer);
    }
    announcer.textContent = "";
    requestAnimationFrame(() => (announcer.textContent = message));
  };
  const fallbackCopy = (value) => {
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    if (!copied) throw new Error("copy failed");
  };
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-text]");
    if (!button) return;
    const value = Array.from(button.parentElement.childNodes)
      .filter((node) => node !== button)
      .map((node) => node.textContent)
      .join("")
      .trim();
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
      else fallbackCopy(value);
    } catch (error) {
      try {
        fallbackCopy(value);
      } catch (fallbackError) {
        announce("Textul nu a putut fi copiat.");
        return;
      }
    }
    button.classList.add("is-copied");
    button.setAttribute("aria-label", "Copiat");
    button.title = "Copiat";
    announce("Text copiat.");
    clearTimeout(button.copyTimer);
    button.copyTimer = setTimeout(() => {
      button.classList.remove("is-copied");
      button.setAttribute("aria-label", "Copiază textul");
      button.title = "Copiază textul";
    }, 1600);
  });
})();
