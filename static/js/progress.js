"use strict";
(() => {
  const canvas = document.getElementById("trend-chart");
  const source = document.getElementById("trend-data");
  if (!canvas || !source || !window.Chart) return;
  const days = JSON.parse(source.textContent);
  const totals = days.map((day) => day.total);
  const root = canvas.closest(".trend-card");
  const style = getComputedStyle(root);
  const token = (name) => style.getPropertyValue(name).trim();
  const color = {
    series: token("--series-1"),
    seriesHover: token("--series-1-hover"),
    wash: token("--series-1-wash"),
    grid: token("--chart-grid"),
    axis: token("--chart-axis"),
    muted: token("--chart-muted"),
    ink: token("--chart-ink"),
    border: token("--chart-border"),
  };
  // Romanian plural: 1 greșeală, 2–19 greșeli, 20 de greșeli.
  const romanianMistakes = (n) =>
    n === 1 ? "1 greșeală" : n === 0 || (n % 100 >= 1 && n % 100 <= 19) ? `${n} greșeli` : `${n} de greșeli`;
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = color.muted;

  // A vertical hairline follows the hovered day on the trend view.
  const crosshair = {
    id: "crosshair",
    afterDatasetsDraw(chart) {
      const active = chart.tooltip.getActiveElements();
      if (chart.config.type !== "line" || !active.length) return;
      const { ctx, chartArea } = chart;
      const x = active[0].element.x;
      ctx.save();
      ctx.strokeStyle = color.axis;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      ctx.restore();
    },
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    animation: reducedMotion ? false : { duration: 450 },
    // The whole day slot is the hover target, not just the painted mark.
    interaction: { mode: "index", intersect: false },
    layout: { padding: { top: 6 } },
    scales: {
      x: {
        grid: { display: false },
        border: { color: color.axis },
        ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 6 },
      },
      y: {
        beginAtZero: true,
        suggestedMax: Math.max(4, ...totals),
        grid: { color: color.grid, lineWidth: 1 },
        border: { display: false },
        ticks: { precision: 0, maxTicksLimit: 5, padding: 8 },
      },
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#ffffff",
        borderColor: color.border,
        borderWidth: 1,
        cornerRadius: 10,
        padding: { x: 12, y: 9 },
        displayColors: false,
        titleColor: color.muted,
        titleFont: { size: 12, weight: "500" },
        bodyColor: color.ink,
        bodyFont: { size: 15, weight: "700" },
        caretSize: 0,
        callbacks: {
          title: (items) => days[items[0].dataIndex].long,
          label: (item) =>
            romanianMistakes(item.parsed.y),
        },
      },
    },
  };

  const datasets = {
    trend: {
      data: totals,
      borderColor: color.series,
      borderWidth: 2,
      borderJoinStyle: "round",
      borderCapStyle: "round",
      backgroundColor: color.wash,
      fill: "origin",
      cubicInterpolationMode: "monotone",
      pointRadius: 0,
      pointHitRadius: 12,
      pointHoverRadius: 5,
      pointHoverBackgroundColor: color.series,
      pointHoverBorderColor: "#ffffff",
      pointHoverBorderWidth: 2,
    },
    daily: {
      data: totals,
      backgroundColor: color.series,
      hoverBackgroundColor: color.seriesHover,
      borderRadius: { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 },
      borderSkipped: "start",
      maxBarThickness: 24,
      categoryPercentage: 0.92,
      barPercentage: 0.82,
    },
  };

  let chart;
  const buttons = root.querySelectorAll("[data-view]");
  function render(view) {
    if (chart) chart.destroy();
    chart = new Chart(canvas, {
      type: view === "daily" ? "bar" : "line",
      data: { labels: days.map((day) => day.label), datasets: [datasets[view]] },
      options,
      plugins: [crosshair],
    });
    buttons.forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.view === view)),
    );
  }
  buttons.forEach((button) =>
    button.addEventListener("click", () => render(button.dataset.view)),
  );
  // The chart only appears when the library loaded; the table view works without it.
  root.querySelectorAll("[data-chart]").forEach((el) => (el.hidden = false));
  render("trend");
})();
