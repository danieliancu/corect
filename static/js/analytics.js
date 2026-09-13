"use strict";
(() => {
  if (!window.Chart) return;
  document.querySelectorAll("[data-usage-chart]").forEach((canvas) => {
    const source = document.getElementById(canvas.dataset.usageChart);
    if (!source) return;
    const rows = JSON.parse(source.textContent);
    // The values table stays available; the chart only appears once the library has loaded.
    canvas.hidden = false;
    new Chart(canvas, {
      type: "bar",
      data: {
        labels: rows.map((row) => row.label),
        datasets: [
          {
            label: "Requests",
            data: rows.map((row) => row.requests),
            backgroundColor: "#247cff",
            hoverBackgroundColor: "#0860e8",
            borderRadius: { topLeft: 4, topRight: 4 },
            borderSkipped: "start",
            maxBarThickness: 24,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: matchMedia("(prefers-reduced-motion: reduce)").matches ? false : undefined,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { autoSkip: true, maxTicksLimit: 10, maxRotation: 0 } },
          y: { beginAtZero: true, ticks: { precision: 0, maxTicksLimit: 5 } },
        },
      },
    });
  });
})();
