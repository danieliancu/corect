"use strict";
// First-party business funnel (apps/analytics/funnel.py). Loaded only when analytics is allowed; sends a step name and
// a placement, never text, and the server stores each step once per day per account or anonymous visitor.
(() => {
  const script = document.currentScript;
  const url = script?.dataset.funnelUrl;
  if (!url) return;
  const csrfToken = () =>
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1] || "";

  const send = (name, placement = "") => {
    const body = new FormData();
    body.append("name", name);
    body.append("placement", placement);
    body.append("csrfmiddlewaretoken", csrfToken());
    // sendBeacon survives the page navigating away (a click on a link); fetch is the fallback.
    if (!(navigator.sendBeacon && navigator.sendBeacon(url, body))) {
      fetch(url, { method: "POST", body, credentials: "same-origin", keepalive: true }).catch(() => {});
    }
  };

  const oncePerDay = (key, action) => {
    const today = new Date().toISOString().slice(0, 10);
    try {
      if (localStorage.getItem(key) === today) return;
      localStorage.setItem(key, today);
    } catch {
      // Storage unavailable (private mode): the server still keeps one row a day.
    }
    action();
  };

  oncePerDay("corect_funnel_visit", () => send("site_visit"));
  if (document.querySelector("[data-funnel-signup]")) send("signup_viewed");

  const plans = document.querySelector("[data-funnel-pricing]");
  if (plans && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      send("pricing_viewed", plans.dataset.funnelPricing);
    }, { threshold: 0.4 });
    observer.observe(plans);
  }

  document.addEventListener("click", (event) => {
    const cta = event.target.closest("[data-funnel-cta]");
    if (cta) send("pro_cta_clicked", cta.dataset.funnelCta);
  });
})();
