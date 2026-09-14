"use strict";
// The notice bar and the cookie settings dialog. Both forms work without JavaScript; this only avoids reloading the
// page for "Am înțeles" and opens the settings in a dialog instead of on /cookie-uri/.
(() => {
  const bar = document.querySelector("[data-consent-bar]");
  bar?.querySelector("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const response = await fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" });
      if (!response.ok) throw new Error("consent_failed");
      bar.remove();
    } catch {
      form.submit();
    }
  });
  const dialog = document.getElementById("consent-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return;
  dialog.querySelector("[data-consent-close]")?.addEventListener("click", () => dialog.close());
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-consent-open]")) return;
    event.preventDefault();
    if (!dialog.open) dialog.showModal();
  });
})();
