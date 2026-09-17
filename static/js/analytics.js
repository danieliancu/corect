"use strict";
// Staff analytics: the sidebar drawer, filters that submit themselves, the main activity chart and the KPI sparklines.
// Every chart is an enhancement — the figures are always in the page as a table, so nothing depends on Chart.js.
(() => {
  const BLUE = "#247cff";
  const BLUE_DARK = "#0860e8";
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

  const readSeries = (id) => {
    const source = document.getElementById(id);
    if (!source) return null;
    try {
      return JSON.parse(source.textContent);
    } catch (error) {
      return null;
    }
  };

  // ----- Sidebar drawer (tablet and phone) -----
  const sidebar = document.querySelector("[data-analytics-sidebar]");
  const burger = document.querySelector("[data-analytics-burger]");
  const scrim = document.querySelector("[data-analytics-scrim]");
  if (sidebar && burger && scrim) {
    const setOpen = (open) => {
      sidebar.toggleAttribute("data-open", open);
      scrim.hidden = !open;
      burger.setAttribute("aria-expanded", String(open));
      burger.setAttribute("aria-label", open ? "Hide the menu" : "Show the menu");
    };
    burger.addEventListener("click", () => setOpen(!sidebar.hasAttribute("data-open")));
    scrim.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && sidebar.hasAttribute("data-open")) {
        setOpen(false);
        burger.focus();
      }
    });
    // Following an anchor into the page should close the drawer behind it.
    sidebar.querySelectorAll("[data-analytics-anchor]").forEach((link) =>
      link.addEventListener("click", () => setOpen(false)),
    );
  }

  // ----- Filters submit as soon as one changes; the Apply button still works without JavaScript -----
  const filters = document.querySelector("[data-analytics-filters]");
  if (filters) {
    filters.addEventListener("change", (event) => {
      if (event.target.matches("select, input[type=radio]")) filters.requestSubmit();
    });
  }

  // ----- Which section of the overview is in view -----
  const anchors = [...document.querySelectorAll("[data-analytics-anchor]")];
  const sections = anchors
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter(Boolean);
  if (sections.length && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting);
        if (!visible.length) return;
        const top = visible.reduce((best, entry) => (entry.boundingClientRect.top < best.boundingClientRect.top ? entry : best));
        anchors.forEach((link) =>
          link.toggleAttribute("aria-current", link.getAttribute("href") === `#${top.target.id}`),
        );
      },
      { rootMargin: "-80px 0px -70% 0px" },
    );
    sections.forEach((section) => observer.observe(section));
  }

  if (!window.Chart) return;

  // ----- The overview's main chart: requests or AI cost over the same days -----
  document.querySelectorAll("[data-usage-chart]").forEach((canvas) => {
    const rows = readSeries(canvas.dataset.usageChart);
    if (!rows) return;
    const labels = rows.map((row) => row.label);
    const metrics = {
      requests: { label: "Requests", values: rows.map((row) => row.requests ?? 0) },
      cost: { label: "Text AI cost (USD)", values: rows.map((row) => row.cost ?? 0) },
    };
    let metric = "requests";
    canvas.hidden = false;
    const gradient = canvas.getContext("2d").createLinearGradient(0, 0, 0, 260);
    gradient.addColorStop(0, "#247cff33");
    gradient.addColorStop(1, "#247cff03");
    const chart = new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: metrics[metric].label,
            data: metrics[metric].values,
            borderColor: BLUE_DARK,
            backgroundColor: gradient,
            borderWidth: 2.5,
            fill: true,
            tension: 0.35,
            pointRadius: labels.length > 45 ? 0 : 3,
            pointBackgroundColor: "#fff",
            pointBorderColor: BLUE_DARK,
            pointBorderWidth: 2,
            pointHoverRadius: 5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: reducedMotion.matches ? false : undefined,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#101a3a",
            padding: 10,
            cornerRadius: 8,
            displayColors: false,
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { autoSkip: true, maxTicksLimit: 10, maxRotation: 0 }, border: { display: false } },
          y: {
            beginAtZero: true,
            border: { display: false },
            grid: { color: "#eef2fa" },
            ticks: { maxTicksLimit: 5 },
          },
        },
      },
    });
    document.querySelectorAll("[data-analytics-metric]").forEach((button) => {
      button.addEventListener("click", () => {
        metric = button.dataset.analyticsMetric;
        document.querySelectorAll("[data-analytics-metric]").forEach((other) =>
          other.setAttribute("aria-pressed", String(other === button)),
        );
        chart.data.datasets[0].label = metrics[metric].label;
        chart.data.datasets[0].data = metrics[metric].values;
        chart.update();
      });
    });
  });

  // ----- KPI sparklines, drawn only from a real per-day series -----
  document.querySelectorAll("[data-analytics-spark]").forEach((canvas) => {
    const rows = readSeries(canvas.dataset.analyticsSpark);
    const key = canvas.dataset.sparkKey;
    if (!rows || rows.length < 2) return;
    const values = rows.map((row) => row[key] ?? 0);
    if (!values.some((value) => value)) return; // Nothing happened: a flat line would say more than it knows.
    // The wrapper carries the height; showing it before drawing gives Chart.js a box to measure.
    if (canvas.parentElement) canvas.parentElement.hidden = false;
    new Chart(canvas, {
      type: "line",
      data: {
        labels: rows.map((row) => row.label),
        datasets: [{ data: values, borderColor: BLUE, borderWidth: 2, fill: false, tension: 0.4, pointRadius: 0 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: reducedMotion.matches ? false : undefined,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false } },
        elements: { line: { capBezierPoints: true } },
      },
    });
  });
})();
