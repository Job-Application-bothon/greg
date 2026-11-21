/** Interactive SVG balance & forecast chart with zoom, pan, range scrub, and crosshair tooltip. */
import { escapeHtml, fmtMoney, fmtZero } from "./format.js";

const WHATIF_COLOR = "#2f6fdf";

export function buildChartPoints(state, whatIf = null, loan = null) {
  const la = (state && state.liquidity_agent) || {};
  const gran = la.granularity === "month" ? "month" : "day";
  const fore = la.forecast_series || [];

  if (gran === "month") {
    const hist = state.monthly_history_series || [];
    return [
      ...hist.map((p) => ({
        date: p.date,
        fullDate: p.date,
        bal: Number(p.balance),
        kind: "hist",
      })),
      ...fore.map((p, i) => ({
        date: p.date,
        fullDate: p.date,
        bal: Number(p.balance),
        balPess: p.balance_pessimistic != null ? Number(p.balance_pessimistic) : null,
        balCredit: loan ? loan[i] : p.balance_with_credit != null ? Number(p.balance_with_credit) : null,
        balWhatIf: whatIf ? whatIf[i] : null,
        expectedIn: p.expected_in != null ? Number(p.expected_in) : null,
        expectedOut: p.expected_out != null ? Number(p.expected_out) : null,
        kind: "fore",
      })),
    ];
  }

  const hist = state.history_series || [];
  return [
    ...hist.map((p) => ({
      date: p.date,
      fullDate: p.date,
      bal: Number(p.balance),
      kind: "hist",
    })),
    ...fore.map((p, i) => ({
      date: p.date,
      fullDate: p.date,
      bal: Number(p.balance),
      balCredit: loan ? loan[i] : p.balance_with_credit != null ? Number(p.balance_with_credit) : null,
      balWhatIf: whatIf ? whatIf[i] : null,
      expectedIn: p.expected_in != null ? Number(p.expected_in) : null,
      expectedOut: p.expected_out != null ? Number(p.expected_out) : null,
      kind: "fore",
    })),
  ];
}

export function createChartController(
  els,
  { getCurrentState, getWhatIf = () => null, getLoan = () => null }
) {
  const {
    forecastPanel,
    forecastChart,
    chartTooltip,
    btnZoomIn,
    btnZoomOut,
    btnZoomReset,
    chartScroll,
    chartRangeLabel,
    chartLegend,
  } = els;

  let chartHoverMeta = null;
  let chartZoom = { key: null, start: 0, end: 0 };
  let chartDrag = null;

  function resetZoom() {
    chartZoom = { key: null, start: 0, end: 0 };
    chartDrag = null;
  }

  function render(state) {
    const la = (state && state.liquidity_agent) || {};
    const currency = (state && state.currency) || "EUR";
    const interval = la.granularity === "month" ? "month" : "day";
    const histPtsCount =
      interval === "month"
        ? ((state && state.monthly_history_series) || []).length
        : ((state && state.history_series) || []).length;
    const fore = la.forecast_series || [];

    chartTooltip.classList.add("hidden");
    if (histPtsCount === 0 && fore.length === 0) {
      chartHoverMeta = null;
      forecastChart.innerHTML = "";
      chartScroll.max = "0";
      chartScroll.value = "0";
      chartScroll.disabled = true;
      chartRangeLabel.textContent = "—";
      if (chartLegend) chartLegend.classList.add("hidden");
      return;
    }

    const loan = getLoan(state);
    const hasApprovedCredit =
      !!loan || !!(state && state.approved_credit && Number(state.approved_credit.amount) > 0);
    const creditLabel = loan ? "With Commerzbank loan" : "With approved credit";
    const creditTip = loan ? "With loan" : "With credit";

    const whatIf = getWhatIf(state);
    const allPoints = buildChartPoints(state, whatIf, loan);
    const total = allPoints.length;
    const defaultWindow = interval === "month" ? 24 : 60;
    const defaultSpan = Math.min(total, defaultWindow);
    const defaultStart = Math.max(0, total - defaultSpan);
    const defaultEnd = Math.max(0, total - 1);

    const zoomKey = `${interval}:${histPtsCount}:${fore.length}:${total}`;
    if (chartZoom.key !== zoomKey) {
      chartZoom = { key: zoomKey, start: defaultStart, end: defaultEnd };
    }
    const visStart = Math.max(0, Math.min(total - 1, chartZoom.start));
    const visEnd = Math.max(visStart, Math.min(total - 1, chartZoom.end));
    chartZoom.start = visStart;
    chartZoom.end = visEnd;
    const visPoints = allPoints.slice(visStart, visEnd + 1);
    const curSpan = visPoints.length;
    const isDefaultWindow = visStart === defaultStart && visEnd === defaultEnd;
    btnZoomReset.disabled = isDefaultWindow;
    btnZoomIn.disabled = curSpan <= 5;
    btnZoomOut.disabled = visStart === 0 && visEnd === total - 1;

    const maxScroll = Math.max(0, total - curSpan);
    chartScroll.max = String(maxScroll);
    chartScroll.value = String(visStart);
    chartScroll.disabled = maxScroll === 0;

    const firstDate = visPoints[0].date;
    const lastDate = visPoints[visPoints.length - 1].date;
    chartRangeLabel.textContent = `${firstDate} → ${lastDate} (${curSpan} of ${total})`;

    const dataValues = [];
    visPoints.forEach((p) => {
      dataValues.push(p.bal);
      if (p.kind === "fore" && p.balPess != null) {
        dataValues.push(p.balPess);
      }
      if (hasApprovedCredit && p.kind === "fore" && p.balCredit != null) {
        dataValues.push(p.balCredit);
      }
      if (p.kind === "fore" && p.balWhatIf != null) {
        dataValues.push(p.balWhatIf);
      }
    });

    const dataMin = Math.min(...dataValues);
    const dataMax = Math.max(...dataValues);
    let scaleMin = Math.min(0, dataMin);
    let scaleMax = Math.max(0, dataMax);
    if (scaleMin === scaleMax) {
      scaleMin -= 1000;
      scaleMax += 1000;
    }

    const W = Math.max(520, Math.round(forecastChart.clientWidth || 900));
    const H = 260;
    forecastChart.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const leftPad = 94;
    const rightPad = visPoints.some(p => p.kind === "fore") ? 158 : 24;
    const topPad = 24;
    const bottomPad = 34;
    const plotW = W - leftPad - rightPad;
    const plotH = H - topPad - bottomPad;

    const xFor = (localIdx) =>
      leftPad + (visPoints.length <= 1 ? 0 : (localIdx / (visPoints.length - 1)) * plotW);
    const yFor = (v) => topPad + ((scaleMax - v) / (scaleMax - scaleMin)) * plotH;

    const zeroY = yFor(0);
    const maxY = yFor(dataMax);
    const minY = yFor(dataMin);
    let splitGlobalIdx = -1;
    for (let i = allPoints.length - 1; i >= 0; i--) {
      if (allPoints[i].kind === "hist") {
        splitGlobalIdx = i;
        break;
      }
    }
    const splitLocalIdx = splitGlobalIdx - visStart;
    const showToday = splitGlobalIdx >= 0 && splitLocalIdx >= 0 && splitLocalIdx < visPoints.length;
    const splitX = xFor(splitLocalIdx);

    const histCoords = [];
    const foreCoords = [];
    const pessCoords = [];
    const creditCoords = [];
    const whatIfCoords = [];
    visPoints.forEach((p, localIdx) => {
      const xStr = xFor(localIdx).toFixed(1);
      const yStr = yFor(p.bal).toFixed(1);
      if (p.kind === "hist") {
        histCoords.push(`${xStr},${yStr}`);
      } else {
        if (foreCoords.length === 0 && localIdx > 0 && visPoints[localIdx - 1].kind === "hist") {
          const prev = visPoints[localIdx - 1];
          const pxStr = xFor(localIdx - 1).toFixed(1);
          const pyStr = yFor(prev.bal).toFixed(1);
          foreCoords.push(`${pxStr},${pyStr}`);
          if (p.balPess != null) pessCoords.push(`${pxStr},${pyStr}`);
          if (hasApprovedCredit) creditCoords.push(`${pxStr},${pyStr}`);
          if (p.balWhatIf != null) whatIfCoords.push(`${pxStr},${pyStr}`);
        }
        foreCoords.push(`${xStr},${yStr}`);
        if (p.balPess != null) {
          pessCoords.push(`${xStr},${yFor(p.balPess).toFixed(1)}`);
        }
        if (hasApprovedCredit && p.balCredit != null) {
          creditCoords.push(`${xStr},${yFor(p.balCredit).toFixed(1)}`);
        }
        if (p.balWhatIf != null) {
          whatIfCoords.push(`${xStr},${yFor(p.balWhatIf).toFixed(1)}`);
        }
      }
    });

    if (chartLegend) {
      chartLegend.innerHTML = "";
      chartLegend.classList.add("hidden");
    }
    const endLabels = [
      {coords: foreCoords, text: interval === "month" ? "Expected forecast" : "Forecast balance", color: "#805181"},
      {coords: pessCoords, text: "Pessimistic stress test", color: "#c96b7c"},
      {coords: creditCoords, text: creditLabel, color: "#24664e"},
      {coords: whatIfCoords, text: "What-if scenario", color: WHATIF_COLOR},
    ].filter(label => label.coords.length > 1).map(label => {
      const [x, y] = label.coords.at(-1).split(",").map(Number);
      return {...label, x, y, labelY: Math.max(topPad + 8, Math.min(H - bottomPad - 8, y))};
    }).sort((a, b) => a.y - b.y);
    // Keep close endpoints readable without moving their actual line tips.
    for (let i = 1; i < endLabels.length; i++) {
      endLabels[i].labelY = Math.max(endLabels[i].labelY, endLabels[i - 1].labelY + 18);
    }
    for (let i = endLabels.length - 1; i >= 0; i--) {
      endLabels[i].labelY = Math.min(endLabels[i].labelY, i === endLabels.length - 1 ? H - bottomPad - 8 : endLabels[i + 1].labelY - 18);
    }
    const endpointMarkup = endLabels.map(label => `
      <g class="forecast-end-label" data-color="${label.color}" data-x="${label.x}" data-y="${label.y}" data-label-y="${label.labelY}" pointer-events="none">
        <path d="M ${label.x} ${label.y} L ${label.x + 6} ${label.labelY} H ${label.x + 10}" fill="none" stroke="${label.color}" stroke-width="1" />
        <circle cx="${label.x}" cy="${label.y}" r="2.5" fill="${label.color}" />
        <text x="${label.x + 14}" y="${label.labelY}" dominant-baseline="middle" font-size="11" font-weight="500" fill="${label.color}" stroke="white" stroke-width="3" stroke-linejoin="round" paint-order="stroke">${escapeHtml(label.text)}</text>
      </g>`).join("");

    const showMinLabel = Math.abs(minY - maxY) >= 14;
    const showZeroLabel =
      Math.abs(zeroY - maxY) >= 14 && (!showMinLabel || Math.abs(zeroY - minY) >= 14);
    const clampLabelY = (y) => Math.max(topPad + 6, Math.min(H - bottomPad + 4, y + 4));

    chartHoverMeta = {
      allPoints,
      visPoints,
      visStart,
      visEnd,
      defaultStart,
      defaultEnd,
      xFor,
      yFor,
      W,
      H,
      leftPad,
      rightPad,
      topPad,
      bottomPad,
      plotW,
      plotH,
      currency,
      hasApprovedCredit,
      creditTip,
    };

    forecastChart.innerHTML = `
      <line x1="${leftPad}" y1="${maxY.toFixed(1)}" x2="${W - rightPad}" y2="${maxY.toFixed(1)}" stroke="#eeeeee" stroke-dasharray="2 3" stroke-width="1" />
      <line x1="${leftPad}" y1="${minY.toFixed(1)}" x2="${W - rightPad}" y2="${minY.toFixed(1)}" stroke="#eeeeee" stroke-dasharray="2 3" stroke-width="1" />
      <line x1="${leftPad}" y1="${zeroY.toFixed(1)}" x2="${W - rightPad}" y2="${zeroY.toFixed(1)}" stroke="#bdbdbd" stroke-dasharray="4 4" stroke-width="1.5" />

      <text x="${leftPad - 8}" y="${clampLabelY(maxY).toFixed(1)}" text-anchor="end" font-size="11" fill="#3d3d3d">${fmtMoney(dataMax, currency)}</text>
      ${
        showMinLabel
          ? `<text x="${leftPad - 8}" y="${clampLabelY(minY).toFixed(1)}" text-anchor="end" font-size="11" fill="#3d3d3d">${fmtMoney(dataMin, currency)}</text>`
          : ""
      }
      ${
        showZeroLabel
          ? `<text x="${leftPad - 8}" y="${clampLabelY(zeroY).toFixed(1)}" text-anchor="end" font-size="11" fill="#6e6e6e">${escapeHtml(fmtZero(currency))}</text>`
          : ""
      }

      ${
        showToday
          ? `<line x1="${splitX.toFixed(1)}" y1="${topPad}" x2="${splitX.toFixed(1)}" y2="${H - bottomPad}" stroke="#d5d5d5" stroke-dasharray="3 3" stroke-width="1" />
             <text x="${splitX.toFixed(1)}" y="${topPad - 7}" text-anchor="middle" font-size="11" fill="#3d3d3d">Today</text>`
          : ""
      }

      ${
        histCoords.length > 1
          ? `<polyline fill="none" stroke="#0d0d0d" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" points="${histCoords.join(" ")}" />`
          : ""
      }
      ${
        pessCoords.length > 1
          ? `<polyline fill="none" stroke="#c96b7c" stroke-width="1.8" stroke-dasharray="3 4" stroke-linecap="round" stroke-linejoin="round" points="${pessCoords.join(" ")}" />`
          : ""
      }
      ${
        foreCoords.length > 1
          ? `<polyline fill="none" stroke="#805181" stroke-width="2.5" stroke-dasharray="6 4" stroke-linecap="round" stroke-linejoin="round" points="${foreCoords.join(" ")}" />`
          : ""
      }
      ${
        hasApprovedCredit && creditCoords.length > 1
          ? `<polyline fill="none" stroke="#24664e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" points="${creditCoords.join(" ")}" />`
          : ""
      }

      ${
        whatIfCoords.length > 1
          ? `<polyline fill="none" stroke="${WHATIF_COLOR}" stroke-width="2.5" stroke-dasharray="2 4" stroke-linecap="round" stroke-linejoin="round" points="${whatIfCoords.join(" ")}" />`
          : ""
      }



      ${endpointMarkup}
      <line id="chart-crosshair" x1="0" y1="${topPad}" x2="0" y2="${H - bottomPad}" stroke="#8a8a8a" stroke-dasharray="3 3" stroke-width="1" visibility="hidden" />
      <line id="chart-crosshair-y" x1="${leftPad}" y1="0" x2="${W - rightPad}" y2="0" stroke="#8a8a8a" stroke-dasharray="3 3" stroke-width="1" visibility="hidden" />
      <circle id="chart-hover-pess-dot" cx="0" cy="0" r="4" fill="#c96b7c" stroke="#ffffff" stroke-width="1.5" visibility="hidden" />
      <circle id="chart-hover-dot" cx="0" cy="0" r="5" fill="#0d0d0d" stroke="#ffffff" stroke-width="2" visibility="hidden" />
      <circle id="chart-hover-whatif-dot" cx="0" cy="0" r="4.5" fill="${WHATIF_COLOR}" stroke="#ffffff" stroke-width="2" visibility="hidden" />
      <circle id="chart-hover-credit-dot" cx="0" cy="0" r="4.5" fill="#24664e" stroke="#ffffff" stroke-width="2" visibility="hidden" />
      <rect id="chart-plot-area" x="${leftPad}" y="${topPad}" width="${plotW}" height="${plotH}" fill="transparent" pointer-events="all" />

      <text x="${leftPad}" y="${H - 10}" font-size="11" fill="#6e6e6e">${firstDate}</text>
      <text x="${W - rightPad}" y="${H - 10}" text-anchor="end" font-size="11" fill="#6e6e6e">${lastDate}</text>
    `;
  }

  function applyZoom(factor, anchorRatio = 0.5) {
    if (!chartHoverMeta || !chartHoverMeta.allPoints.length) return;
    const total = chartHoverMeta.allPoints.length;
    if (total <= 5) return;
    const curSpan = chartZoom.end - chartZoom.start + 1;
    const minSpan = Math.min(total, 5);
    const newSpan = Math.max(minSpan, Math.min(total, Math.round(curSpan * factor)));
    if (newSpan === curSpan && !(factor > 1 && newSpan === total)) return;

    const anchorIdx = chartZoom.start + Math.round(anchorRatio * (curSpan - 1));
    let newStart = Math.round(anchorIdx - anchorRatio * (newSpan - 1));
    newStart = Math.max(0, Math.min(total - newSpan, newStart));
    chartZoom.start = newStart;
    chartZoom.end = newStart + newSpan - 1;
    render(getCurrentState());
  }

  function hideHover() {
    chartTooltip.classList.add("hidden");
    const chX = document.getElementById("chart-crosshair");
    const chY = document.getElementById("chart-crosshair-y");
    const dot = document.getElementById("chart-hover-dot");
    const pDot = document.getElementById("chart-hover-pess-dot");
    const cDot = document.getElementById("chart-hover-credit-dot");
    const wDot = document.getElementById("chart-hover-whatif-dot");
    if (wDot) wDot.setAttribute("visibility", "hidden");
    if (chX) chX.setAttribute("visibility", "hidden");
    if (chY) chY.setAttribute("visibility", "hidden");
    if (dot) dot.setAttribute("visibility", "hidden");
    if (pDot) pDot.setAttribute("visibility", "hidden");
    if (cDot) cDot.setAttribute("visibility", "hidden");
  }

  function getHoverRatio(clientX) {
    if (!chartHoverMeta) return 0.5;
    const ctm = forecastChart.getScreenCTM();
    if (!ctm) return 0.5;
    const pt = forecastChart.createSVGPoint();
    pt.x = clientX;
    pt.y = 0;
    const svgPt = pt.matrixTransform(ctm.inverse());
    return chartHoverMeta.plotW > 0
      ? Math.max(0, Math.min(1, (svgPt.x - chartHoverMeta.leftPad) / chartHoverMeta.plotW))
      : 0.5;
  }

  function handleHover(clientX) {
    if (chartDrag || !chartHoverMeta || !chartHoverMeta.visPoints.length) return;
    const m = chartHoverMeta;
    const ctm = forecastChart.getScreenCTM();
    const panelRect = forecastPanel.getBoundingClientRect();
    if (!ctm || !panelRect.width) return;

    const ratio = getHoverRatio(clientX);
    const localIdx =
      m.visPoints.length <= 1
        ? 0
        : Math.max(0, Math.min(m.visPoints.length - 1, Math.round(ratio * (m.visPoints.length - 1))));

    const pt = m.visPoints[localIdx];
    const pxSvg = m.xFor(localIdx);
    const pySvg = m.yFor(pt.bal);

    const chX = document.getElementById("chart-crosshair");
    const chY = document.getElementById("chart-crosshair-y");
    const dot = document.getElementById("chart-hover-dot");
    const pDot = document.getElementById("chart-hover-pess-dot");
    const cDot = document.getElementById("chart-hover-credit-dot");
    const wDot = document.getElementById("chart-hover-whatif-dot");

    if (wDot) {
      if (pt.kind === "fore" && pt.balWhatIf != null) {
        wDot.setAttribute("cx", pxSvg.toFixed(1));
        wDot.setAttribute("cy", m.yFor(pt.balWhatIf).toFixed(1));
        wDot.setAttribute("visibility", "visible");
      } else {
        wDot.setAttribute("visibility", "hidden");
      }
    }
    if (chX) {
      chX.setAttribute("x1", pxSvg.toFixed(1));
      chX.setAttribute("x2", pxSvg.toFixed(1));
      chX.setAttribute("visibility", "visible");
    }
    if (chY) {
      chY.setAttribute("y1", pySvg.toFixed(1));
      chY.setAttribute("y2", pySvg.toFixed(1));
      chY.setAttribute("visibility", "visible");
    }
    if (dot) {
      dot.setAttribute("cx", pxSvg.toFixed(1));
      dot.setAttribute("cy", pySvg.toFixed(1));
      dot.setAttribute("fill", pt.kind === "hist" ? "#0d0d0d" : "#805181");
      dot.setAttribute("visibility", "visible");
    }
    if (pDot) {
      if (pt.kind === "fore" && pt.balPess != null) {
        pDot.setAttribute("cx", pxSvg.toFixed(1));
        pDot.setAttribute("cy", m.yFor(pt.balPess).toFixed(1));
        pDot.setAttribute("visibility", "visible");
      } else {
        pDot.setAttribute("visibility", "hidden");
      }
    }
    if (cDot) {
      if (m.hasApprovedCredit && pt.kind === "fore" && pt.balCredit != null) {
        cDot.setAttribute("cx", pxSvg.toFixed(1));
        cDot.setAttribute("cy", m.yFor(pt.balCredit).toFixed(1));
        cDot.setAttribute("visibility", "visible");
      } else {
        cDot.setAttribute("visibility", "hidden");
      }
    }

    const kindLabel = pt.kind === "hist" ? "Past" : "Forecast";
    const pessHtml =
      pt.kind === "fore" && pt.balPess != null
        ? `<div class="tt-pess">Stress test: ${escapeHtml(fmtMoney(pt.balPess, m.currency))}</div>`
        : "";
    const flowsHtml =
      pt.kind === "fore" && pt.expectedIn != null && pt.expectedOut != null
        ? `<div class="tt-flows">In +${escapeHtml(fmtMoney(pt.expectedIn, m.currency))} · Out -${escapeHtml(fmtMoney(pt.expectedOut, m.currency))}</div>`
        : "";
    const creditHtml =
      m.hasApprovedCredit && pt.kind === "fore" && pt.balCredit != null
        ? `<div class="tt-credit">${m.creditTip}: ${escapeHtml(fmtMoney(pt.balCredit, m.currency))}</div>`
        : "";

    const whatIfHtml =
      pt.kind === "fore" && pt.balWhatIf != null
        ? `<div class="tt-whatif">What-if: ${escapeHtml(fmtMoney(pt.balWhatIf, m.currency))}</div>`
        : "";

    chartTooltip.innerHTML = `
      <div class="tt-date">${escapeHtml(pt.date)} · ${kindLabel}</div>
      <div class="tt-bal">${escapeHtml(fmtMoney(pt.bal, m.currency))}</div>
      ${whatIfHtml}
      ${pessHtml}
      ${flowsHtml}
      ${creditHtml}
    `;
    chartTooltip.classList.remove("hidden");

    const dotSvgPt = forecastChart.createSVGPoint();
    dotSvgPt.x = pxSvg;
    dotSvgPt.y = pySvg;
    const screenDot = dotSvgPt.matrixTransform(ctm);
    const anchorX = screenDot.x - panelRect.left;
    const anchorY = screenDot.y - panelRect.top;
    const tipW = chartTooltip.offsetWidth || 140;
    const tipH = chartTooltip.offsetHeight || 54;

    let left = anchorX + 14;
    if (left + tipW > panelRect.width - 8) {
      left = anchorX - tipW - 14;
    }
    left = Math.max(8, left);
    const top = Math.max(8, Math.min(panelRect.height - tipH - 8, anchorY - tipH / 2));

    chartTooltip.style.left = `${left.toFixed(0)}px`;
    chartTooltip.style.top = `${top.toFixed(0)}px`;
  }

  btnZoomIn.addEventListener("click", () => applyZoom(0.65, 0.5));
  btnZoomOut.addEventListener("click", () => applyZoom(1.5, 0.5));
  btnZoomReset.addEventListener("click", () => {
    if (!chartHoverMeta) return;
    chartZoom = {
      key: chartZoom.key,
      start: chartHoverMeta.defaultStart,
      end: chartHoverMeta.defaultEnd,
    };
    render(getCurrentState());
  });

  chartScroll.addEventListener("input", () => {
    if (!chartHoverMeta) return;
    const total = chartHoverMeta.allPoints.length;
    const span = chartZoom.end - chartZoom.start + 1;
    const newStart = Math.max(0, Math.min(total - span, Number(chartScroll.value || 0)));
    if (newStart !== chartZoom.start) {
      chartZoom.start = newStart;
      chartZoom.end = newStart + span - 1;
      render(getCurrentState());
    }
  });

  forecastChart.addEventListener(
    "wheel",
    (e) => {
      if (!chartHoverMeta || chartHoverMeta.allPoints.length <= 5) return;
      e.preventDefault();
      const total = chartHoverMeta.allPoints.length;
      const span = chartZoom.end - chartZoom.start + 1;
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY) && span < total) {
        const step =
          Math.round((e.deltaX / 240) * Math.max(4, span * 0.25)) || (e.deltaX > 0 ? 1 : -1);
        const newStart = Math.max(0, Math.min(total - span, chartZoom.start + step));
        if (newStart !== chartZoom.start) {
          chartZoom.start = newStart;
          chartZoom.end = newStart + span - 1;
          render(getCurrentState());
        }
      } else {
        const ratio = getHoverRatio(e.clientX);
        applyZoom(e.deltaY < 0 ? 0.78 : 1.28, ratio);
      }
      handleHover(e.clientX);
    },
    { passive: false }
  );

  forecastChart.addEventListener("mousedown", (e) => {
    if (!chartHoverMeta) return;
    const span = chartZoom.end - chartZoom.start + 1;
    if (span >= chartHoverMeta.allPoints.length) return;
    chartDrag = {
      startX: e.clientX,
      origStart: chartZoom.start,
      origEnd: chartZoom.end,
    };
    forecastChart.classList.add("panning");
    hideHover();
  });

  window.addEventListener("mousemove", (e) => {
    if (!chartDrag || !chartHoverMeta) return;
    const plotArea = document.getElementById("chart-plot-area");
    const plotWidthPx = plotArea ? plotArea.getBoundingClientRect().width : 700;
    const span = chartDrag.origEnd - chartDrag.origStart + 1;
    const total = chartHoverMeta.allPoints.length;
    const dx = e.clientX - chartDrag.startX;
    const shiftPts = Math.round((-dx / Math.max(1, plotWidthPx)) * span);
    const newStart = Math.max(0, Math.min(total - span, chartDrag.origStart + shiftPts));
    if (newStart !== chartZoom.start) {
      chartZoom.start = newStart;
      chartZoom.end = newStart + span - 1;
      render(getCurrentState());
    }
  });

  window.addEventListener("mouseup", (e) => {
    if (!chartDrag) return;
    chartDrag = null;
    forecastChart.classList.remove("panning");
    if (forecastChart.contains(e.target)) {
      handleHover(e.clientX);
    } else {
      hideHover();
    }
  });

  window.addEventListener("resize", () => {
    const state = getCurrentState();
    if (state && state.has_data) {
      render(state);
    }
  });

  forecastChart.addEventListener("mousemove", (e) => handleHover(e.clientX));
  forecastChart.addEventListener("mouseleave", hideHover);
  forecastChart.addEventListener(
    "touchmove",
    (e) => {
      if (e.touches && e.touches[0]) handleHover(e.touches[0].clientX);
    },
    { passive: true }
  );
  forecastChart.addEventListener("touchend", hideHover);

  return { render, resetZoom };
}
