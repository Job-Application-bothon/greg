/** Interactive SVG balance & forecast chart with zoom, pan, mini-map navigator, focus pop-up, and Ctrl+drag period selection. */
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
        income: Number(p.income || 0),
        expenses: Number(p.expenses || 0),
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
        income: Number(p.expected_in || 0),
        expenses: Number(p.expected_out || 0),
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
      income: Number(p.income || 0),
      expenses: Number(p.expenses || 0),
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
      income: Number(p.expected_in || 0),
      expenses: Number(p.expected_out || 0),
      kind: "fore",
    })),
  ];
}

export function createChartController(
  els,
  { getCurrentState, getWhatIf = () => null, getLoan = () => null, onAnalyzePeriod = null }
) {
  const {
    forecastPanel,
    forecastChart,
    chartTooltip,
    chartFocusCard,
    btnZoomIn,
    btnZoomOut,
    btnZoomReset,
    chartRangeLabel,
    chartLegend,
    minimapTrack,
    minimapSvg,
    minimapMaskLeft,
    minimapMaskRight,
    minimapWindow,
    minimapHandleLeft,
    minimapHandleRight,
    periodActionBarWrap,
    periodRangeDatesEl,
    btnAnalyzePeriod,
    btnClearPeriod,
  } = els;

  let chartHoverMeta = null;
  let chartZoom = { key: null, start: 0, end: 0 };
  let chartDrag = null;
  let minimapDrag = null;
  let periodDrag = null;
  let selectedFocusDate = "";
  let focusCardExpanded = false;
  let selectedPeriod = null;

  function resetZoom() {
    chartZoom = { key: null, start: 0, end: 0 };
    chartDrag = null;
    minimapDrag = null;
  }

  function resetAll() {
    resetZoom();
    periodDrag = null;
    selectedFocusDate = "";
    focusCardExpanded = false;
    selectedPeriod = null;
    if (forecastChart) forecastChart.classList.remove("period-selecting", "panning");
    if (chartFocusCard) chartFocusCard.classList.add("hidden");
    if (periodActionBarWrap) periodActionBarWrap.classList.remove("open");
  }

  function clearFocus(rerender = true) {
    selectedFocusDate = "";
    focusCardExpanded = false;
    if (chartFocusCard) chartFocusCard.classList.add("hidden");
    const state = getCurrentState();
    if (rerender && state && state.has_data) {
      render(state);
    }
  }

  function clearSelectedPeriod(rerender = true) {
    selectedPeriod = null;
    periodDrag = null;
    if (forecastChart) forecastChart.classList.remove("period-selecting");
    if (periodActionBarWrap) periodActionBarWrap.classList.remove("open");
    const state = getCurrentState();
    if (rerender && state && state.has_data) {
      render(state);
    }
  }

  function renderMiniMap(allPoints, splitGlobalIdx, visStart, visEnd) {
    if (!minimapSvg || !allPoints.length) return;
    const total = allPoints.length;
    const MW = Math.max(300, Math.round((minimapTrack && minimapTrack.clientWidth) || 900));
    const MH = 42;
    minimapSvg.setAttribute("viewBox", `0 0 ${MW} ${MH}`);

    const vals = allPoints.map((p) => p.bal);
    let minV = Math.min(...vals, 0);
    let maxV = Math.max(...vals, 0);
    if (minV === maxV) {
      minV -= 1000;
      maxV += 1000;
    }

    const xForMini = (idx) => (total <= 1 ? 0 : (idx / (total - 1)) * MW);
    const yForMini = (v) => 4 + ((maxV - v) / (maxV - minV)) * (MH - 8);
    const baseBottom = MH;

    const histMiniPts = [];
    const foreMiniPts = [];
    allPoints.forEach((p, i) => {
      const coord = `${xForMini(i).toFixed(1)},${yForMini(p.bal).toFixed(1)}`;
      if (splitGlobalIdx < 0) {
        foreMiniPts.push(coord);
      } else {
        if (i <= splitGlobalIdx) histMiniPts.push(coord);
        if (i >= splitGlobalIdx) foreMiniPts.push(coord);
      }
    });

    const todayX = splitGlobalIdx >= 0 ? xForMini(splitGlobalIdx) : 0;
    const focusIdx = selectedFocusDate
      ? allPoints.findIndex((p) => p.date === selectedFocusDate)
      : -1;
    const focusMiniX = focusIdx >= 0 ? xForMini(focusIdx) : -1;
    const focusMiniY = focusIdx >= 0 ? yForMini(allPoints[focusIdx].bal) : -1;

    let periodMiniHtml = "";
    if (selectedPeriod && selectedPeriod.startDate && selectedPeriod.endDate) {
      const pStartIdx = allPoints.findIndex((p) => p.date >= selectedPeriod.startDate);
      let pEndIdx = -1;
      for (let i = allPoints.length - 1; i >= 0; i--) {
        if (allPoints[i].date <= selectedPeriod.endDate) {
          pEndIdx = i;
          break;
        }
      }
      if (pStartIdx >= 0 && pEndIdx >= pStartIdx) {
        const pmx1 = xForMini(pStartIdx);
        const pmx2 = xForMini(pEndIdx);
        const pmw = Math.max(3, pmx2 - pmx1);
        periodMiniHtml = `<rect id="minimap-period-band" x="${pmx1.toFixed(1)}" y="0" width="${pmw.toFixed(
          1
        )}" height="${MH}" fill="rgba(255, 201, 255, 0.45)" stroke="#805181" stroke-width="1" />`;
      }
    }

    const histArea =
      histMiniPts.length > 1
        ? `0,${baseBottom} ${histMiniPts.join(" ")} ${todayX.toFixed(1)},${baseBottom}`
        : "";
    const foreArea =
      foreMiniPts.length > 1
        ? `${todayX.toFixed(1)},${baseBottom} ${foreMiniPts.join(" ")} ${MW},${baseBottom}`
        : "";

    minimapSvg.innerHTML = `
      ${histArea ? `<polygon fill="rgba(13, 13, 13, 0.09)" points="${histArea}" />` : ""}
      ${foreArea ? `<polygon fill="rgba(128, 81, 129, 0.12)" points="${foreArea}" />` : ""}
      ${periodMiniHtml}
      ${
        histMiniPts.length > 1
          ? `<polyline fill="none" stroke="#0d0d0d" stroke-width="1.5" points="${histMiniPts.join(" ")}" />`
          : ""
      }
      ${
        foreMiniPts.length > 1
          ? `<polyline fill="none" stroke="#805181" stroke-width="1.5" stroke-dasharray="4 3" points="${foreMiniPts.join(" ")}" />`
          : ""
      }
      ${
        splitGlobalIdx >= 0
          ? `<line x1="${todayX.toFixed(1)}" y1="0" x2="${todayX.toFixed(1)}" y2="${MH}" stroke="#623761" stroke-dasharray="2 2" stroke-width="1.5" />
             <text x="${Math.min(MW - 22, Math.max(22, todayX + 4)).toFixed(1)}" y="11" font-size="9" font-weight="700" fill="#623761">Today</text>`
          : ""
      }
      ${
        focusIdx >= 0
          ? `<line x1="${focusMiniX.toFixed(1)}" y1="0" x2="${focusMiniX.toFixed(1)}" y2="${MH}" stroke="#805181" stroke-width="1.8" />
             <circle cx="${focusMiniX.toFixed(1)}" cy="${focusMiniY.toFixed(1)}" r="3.2" fill="#805181" stroke="#ffffff" stroke-width="1.2" />`
          : ""
      }
    `;

    const denom = Math.max(1, total - 1);
    const leftPct = Math.max(0, Math.min(100, (visStart / denom) * 100));
    const rightPct = Math.max(leftPct + 0.8, Math.min(100, (visEnd / denom) * 100));
    const widthPct = Math.max(0.8, rightPct - leftPct);

    if (minimapMaskLeft) minimapMaskLeft.style.width = `${leftPct.toFixed(2)}%`;
    if (minimapMaskRight) minimapMaskRight.style.width = `${Math.max(0, 100 - rightPct).toFixed(2)}%`;
    if (minimapWindow) {
      minimapWindow.style.left = `${leftPct.toFixed(2)}%`;
      minimapWindow.style.width = `${widthPct.toFixed(2)}%`;
    }
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
      if (minimapSvg) minimapSvg.innerHTML = "";
      if (chartRangeLabel) chartRangeLabel.textContent = "—";
      if (chartLegend) chartLegend.classList.add("hidden");
      if (chartFocusCard) chartFocusCard.classList.add("hidden");
      if (periodActionBarWrap) periodActionBarWrap.classList.remove("open");
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
    if (btnZoomReset) btnZoomReset.disabled = isDefaultWindow && !selectedFocusDate && !selectedPeriod;
    if (btnZoomIn) btnZoomIn.disabled = curSpan <= 5;
    if (btnZoomOut) btnZoomOut.disabled = visStart === 0 && visEnd === total - 1;

    const firstDate = visPoints[0].date;
    const lastDate = visPoints[visPoints.length - 1].date;
    if (chartRangeLabel) {
      chartRangeLabel.textContent = `${firstDate} → ${lastDate} (${curSpan} of ${total})`;
    }

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
    const rightPad = visPoints.some((p) => p.kind === "fore") ? 158 : 24;
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

    renderMiniMap(allPoints, splitGlobalIdx, visStart, visEnd);

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
      {
        coords: foreCoords,
        text: interval === "month" ? "Expected" : "Forecast balance",
        color: "#805181",
      },
      { coords: pessCoords, text: "Pessimistic", color: "#c96b7c" },
      { coords: creditCoords, text: creditLabel, color: "#24664e" },
      { coords: whatIfCoords, text: "What-if scenario", color: WHATIF_COLOR },
    ]
      .filter((label) => label.coords.length > 1)
      .map((label) => {
        const [x, y] = label.coords.at(-1).split(",").map(Number);
        return { ...label, x, y, labelY: Math.max(topPad + 8, Math.min(H - bottomPad - 8, y)) };
      })
      .sort((a, b) => a.y - b.y);
    for (let i = 1; i < endLabels.length; i++) {
      endLabels[i].labelY = Math.max(endLabels[i].labelY, endLabels[i - 1].labelY + 18);
    }
    for (let i = endLabels.length - 1; i >= 0; i--) {
      endLabels[i].labelY = Math.min(
        endLabels[i].labelY,
        i === endLabels.length - 1 ? H - bottomPad - 8 : endLabels[i + 1].labelY - 18
      );
    }
    const endpointMarkup = endLabels
      .map(
        (label) => `
      <g class="forecast-end-label" data-color="${label.color}" data-x="${label.x}" data-y="${label.y}" data-label-y="${label.labelY}" pointer-events="none">
        <path d="M ${label.x} ${label.y} L ${label.x + 6} ${label.labelY} H ${label.x + 10}" fill="none" stroke="${label.color}" stroke-width="1" />
        <circle cx="${label.x}" cy="${label.y}" r="2.5" fill="${label.color}" />
        <text x="${label.x + 14}" y="${label.labelY}" dominant-baseline="middle" font-size="11" font-weight="500" fill="${label.color}" stroke="white" stroke-width="3" stroke-linejoin="round" paint-order="stroke">${escapeHtml(label.text)}</text>
      </g>`
      )
      .join("");

    // Focus point pop-up card & amber marker (NO auto-zoom!)
    let focusMarkerHtml = "";
    let showFocusCard = false;
    if (selectedFocusDate) {
      const fLocalIdx = visPoints.findIndex((p) => p.date === selectedFocusDate);
      if (fLocalIdx >= 0) {
        showFocusCard = true;
        const fp = visPoints[fLocalIdx];
        const fx = xFor(fLocalIdx);
        const fy = yFor(fp.bal);
        const fKind = fp.kind === "hist" ? "Past" : "Forecast";
        const fBalText = fmtMoney(fp.bal, currency);
        const dayInc = Number(fp.income || 0);
        const dayExp = Number(fp.expenses || 0);
        const dayNet = dayInc - dayExp;
        const flowTotal = dayInc + dayExp;
        const incPct = flowTotal > 0 ? Math.round((dayInc / flowTotal) * 100) : 50;
        const expPct = flowTotal > 0 ? 100 - incPct : 50;
        const fIncText = dayInc > 0 ? `+${fmtMoney(dayInc, currency)}` : fmtMoney(0, currency);
        const fExpText = dayExp > 0 ? `-${fmtMoney(dayExp, currency)}` : fmtMoney(0, currency);
        const fNetText =
          dayNet > 0
            ? `+${fmtMoney(dayNet, currency)}`
            : dayNet < 0
            ? fmtMoney(dayNet, currency)
            : fmtMoney(0, currency);
        const fNetClass =
          dayNet > 0 ? "focus-val-income" : dayNet < 0 ? "focus-val-expense" : "";

        focusMarkerHtml = `
          <line id="chart-focus-line" x1="${fx.toFixed(1)}" y1="${topPad}" x2="${fx.toFixed(
          1
        )}" y2="${H - bottomPad}" stroke="#805181" stroke-dasharray="4 3" stroke-width="1.8" />
          <circle cx="${fx.toFixed(1)}" cy="${fy.toFixed(1)}" r="9" fill="rgba(255, 201, 255, 0.45)" />
          <circle id="chart-focus-dot" cx="${fx.toFixed(1)}" cy="${fy.toFixed(
          1
        )}" r="5.5" fill="#805181" stroke="#ffffff" stroke-width="2" style="cursor: pointer" />
        `;

        if (chartFocusCard) {
          const clampedX = Math.max(leftPad + 88, Math.min(W - rightPad - 88, fx));
          const placeBelow = fy < 118;
          const anchorY = placeBelow ? fy + 16 : fy - 14;
          const leftPct = (clampedX / W) * 100;
          const topPx = (anchorY / H) * 260;

          chartFocusCard.style.left = `${leftPct.toFixed(2)}%`;
          chartFocusCard.style.top = `${topPx.toFixed(1)}px`;
          chartFocusCard.classList.remove("hidden");
          chartFocusCard.classList.toggle("below-dot", placeBelow);
          chartFocusCard.classList.toggle("expanded", focusCardExpanded);
          chartFocusCard.setAttribute("aria-expanded", String(focusCardExpanded));
          chartFocusCard.setAttribute("data-inc-pct", String(incPct));
          chartFocusCard.setAttribute("data-exp-pct", String(expPct));

          const barIncW = focusCardExpanded ? incPct : 0;
          const barExpW = focusCardExpanded ? expPct : 0;

          chartFocusCard.innerHTML = `
            <div class="focus-card-top">
              <span class="focus-card-date">${escapeHtml(selectedFocusDate)} · ${fKind}</span>
              <span class="focus-card-actions">
                <span class="focus-card-chevron" aria-hidden="true">▾</span>
                <button type="button" class="focus-card-close" data-focus-close aria-label="Close details">✕</button>
              </span>
            </div>
            <div class="focus-card-bal-row">
              <span class="focus-card-bal">${escapeHtml(fBalText)}</span>
              <span class="focus-card-hint">${focusCardExpanded ? "Hide flow" : "Click for flow"}</span>
            </div>
            <div class="focus-card-drawer">
              <div class="focus-card-drawer-inner">
                <div class="focus-card-divider"></div>
                <div class="focus-flow-row">
                  <span class="focus-flow-label"><span class="focus-dot-inc"></span>Income</span>
                  <span class="focus-val-income">${escapeHtml(fIncText)}</span>
                </div>
                <div class="focus-flow-row">
                  <span class="focus-flow-label"><span class="focus-dot-exp"></span>Expenses</span>
                  <span class="focus-val-expense">${escapeHtml(fExpText)}</span>
                </div>
                <div class="focus-flow-meter" aria-hidden="true">
                  <div class="focus-meter-inc" style="width: ${barIncW}%"></div>
                  <div class="focus-meter-exp" style="width: ${barExpW}%"></div>
                </div>
                <div class="focus-card-divider-bottom"></div>
                <div class="focus-flow-row focus-total-row">
                  <span class="focus-total-label">Total</span>
                  <span class="${fNetClass}">${escapeHtml(fNetText)}</span>
                </div>
              </div>
            </div>
          `;
        }
      }
    }
    if (!showFocusCard && chartFocusCard) {
      chartFocusCard.classList.add("hidden");
    }

    // Ctrl + LMB Selected Period Highlight (light-green band + dark-green boundary dots)
    let periodBandBackHtml = "";
    let periodDotsFrontHtml = "";
    if (selectedPeriod && selectedPeriod.startDate && selectedPeriod.endDate && visPoints.length > 0) {
      const pStart = selectedPeriod.startDate;
      const pEnd = selectedPeriod.endDate;
      const visFirstDate = visPoints[0].date;
      const visLastDate = visPoints[visPoints.length - 1].date;

      if (pEnd >= visFirstDate && pStart <= visLastDate) {
        let sLocalIdx = visPoints.findIndex((p) => p.date >= pStart);
        if (sLocalIdx < 0) sLocalIdx = 0;
        let eLocalIdx = visPoints.length - 1;
        for (let i = visPoints.length - 1; i >= 0; i--) {
          if (visPoints[i].date <= pEnd) {
            eLocalIdx = i;
            break;
          }
        }
        if (eLocalIdx < sLocalIdx) eLocalIdx = sLocalIdx;

        const sx = xFor(sLocalIdx);
        const ex = xFor(eLocalIdx);
        const bandX = Math.min(sx, ex);
        const bandW = Math.max(4, Math.abs(ex - sx));

        periodBandBackHtml = `
          <rect id="chart-period-band" x="${bandX.toFixed(1)}" y="${topPad}" width="${bandW.toFixed(
          1
        )}" height="${plotH}" fill="rgba(255, 201, 255, 0.32)" />
          <line x1="${sx.toFixed(1)}" y1="${topPad}" x2="${sx.toFixed(1)}" y2="${
          H - bottomPad
        }" stroke="#805181" stroke-dasharray="3 3" stroke-width="1.4" />
          <line x1="${ex.toFixed(1)}" y1="${topPad}" x2="${ex.toFixed(1)}" y2="${
          H - bottomPad
        }" stroke="#805181" stroke-dasharray="3 3" stroke-width="1.4" />
        `;

        const sy = yFor(visPoints[sLocalIdx].bal);
        const ey = yFor(visPoints[eLocalIdx].bal);
        periodDotsFrontHtml = `
          <circle cx="${sx.toFixed(1)}" cy="${sy.toFixed(1)}" r="5" fill="#805181" stroke="#ffffff" stroke-width="2" />
          <circle cx="${ex.toFixed(1)}" cy="${ey.toFixed(1)}" r="5" fill="#805181" stroke="#ffffff" stroke-width="2" />
        `;
      }
    }

    if (periodActionBarWrap) {
      const hasPeriod = Boolean(selectedPeriod && selectedPeriod.startDate && selectedPeriod.endDate);
      periodActionBarWrap.classList.toggle("open", hasPeriod);
      if (hasPeriod && periodRangeDatesEl) {
        periodRangeDatesEl.textContent =
          selectedPeriod.startDate === selectedPeriod.endDate
            ? selectedPeriod.startDate
            : `${selectedPeriod.startDate} → ${selectedPeriod.endDate}`;
      }
    }

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
      splitGlobalIdx,
      interval,
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

      ${periodBandBackHtml}

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

      ${periodDotsFrontHtml}
      ${focusMarkerHtml}
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

  function getDateFromChartClientX(clientX) {
    if (!chartHoverMeta || !chartHoverMeta.visPoints.length) return null;
    const ratio = getHoverRatio(clientX);
    const localIdx =
      chartHoverMeta.visPoints.length <= 1
        ? 0
        : Math.max(
            0,
            Math.min(
              chartHoverMeta.visPoints.length - 1,
              Math.round(ratio * (chartHoverMeta.visPoints.length - 1))
            )
          );
    return chartHoverMeta.visPoints[localIdx].date;
  }

  function handleHover(clientX) {
    if (chartDrag || minimapDrag || periodDrag || !chartHoverMeta || !chartHoverMeta.visPoints.length) return;
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

  function toggleFocusCardExpanded() {
    if (!chartFocusCard || chartFocusCard.classList.contains("hidden")) return;
    focusCardExpanded = !focusCardExpanded;
    chartFocusCard.classList.toggle("expanded", focusCardExpanded);
    chartFocusCard.setAttribute("aria-expanded", String(focusCardExpanded));
    const hintEl = chartFocusCard.querySelector(".focus-card-hint");
    if (hintEl) {
      hintEl.textContent = focusCardExpanded ? "Hide flow" : "Click for flow";
    }
    const incBar = chartFocusCard.querySelector(".focus-meter-inc");
    const expBar = chartFocusCard.querySelector(".focus-meter-exp");
    const incPct = Number(chartFocusCard.getAttribute("data-inc-pct") || 50);
    const expPct = Number(chartFocusCard.getAttribute("data-exp-pct") || 50);
    requestAnimationFrame(() => {
      if (incBar) incBar.style.width = focusCardExpanded ? `${incPct}%` : "0%";
      if (expBar) expBar.style.width = focusCardExpanded ? `${expPct}%` : "0%";
    });
  }

  function buildSelectedPeriodPayload() {
    if (!selectedPeriod || !chartHoverMeta || !chartHoverMeta.allPoints.length) return null;
    const allPts = chartHoverMeta.allPoints;
    const todayIdx = chartHoverMeta.splitGlobalIdx;
    const currency = chartHoverMeta.currency || "EUR";
    const monthlyMode = chartHoverMeta.interval === "month";

    let sIdx = allPts.findIndex((p) => p.date >= selectedPeriod.startDate);
    if (sIdx < 0) sIdx = 0;
    let eIdx = -1;
    for (let i = allPts.length - 1; i >= 0; i--) {
      if (allPts[i].date <= selectedPeriod.endDate) {
        eIdx = i;
        break;
      }
    }
    if (eIdx < sIdx) eIdx = sIdx;

    const slice = allPts.slice(sIdx, eIdx + 1);
    if (!slice.length) return null;

    let totalIncome = 0;
    let totalExpenses = 0;
    let lowestBalance = slice[0].bal;
    let lowestDate = slice[0].date;
    let highestBalance = slice[0].bal;
    let highestDate = slice[0].date;

    slice.forEach((p) => {
      totalIncome += Number(p.income || 0);
      totalExpenses += Number(p.expenses || 0);
      if (p.bal < lowestBalance) {
        lowestBalance = p.bal;
        lowestDate = p.date;
      }
      if (p.bal > highestBalance) {
        highestBalance = p.bal;
        highestDate = p.date;
      }
    });

    const startDate = slice[0].date;
    const endDate = slice[slice.length - 1].date;
    const startBalance = slice[0].bal;
    const endBalance = slice[slice.length - 1].bal;
    const balanceDelta = endBalance - startBalance;
    const netFlow = totalIncome - totalExpenses;

    let mode = "past";
    let modeLabel = "Historical Period";
    // These texts skip the chat topic gate; keep them in sync with _CHART_PERIOD_PROMPTS in llm/topic.py.
    let suggestedPrompt = "";

    if (todayIdx >= 0 && eIdx <= todayIdx) {
      mode = "past";
      modeLabel = "Historical Period";
      suggestedPrompt = `Analyze what happened between ${startDate} and ${endDate}: explain the main income drivers, largest expenses, and why my balance changed.`;
    } else if (todayIdx < 0 || sIdx > todayIdx) {
      mode = "future";
      modeLabel = "Forecast Period";
      suggestedPrompt = `Analyze what is most likely going to happen between ${startDate} and ${endDate}: highlight expected upcoming bills, potential cash flow risks, and balance trends.`;
    } else {
      mode = "mixed";
      modeLabel = "Past + Forecast Period";
      suggestedPrompt = `Analyze what happened up to Today and what is most likely going to happen through ${endDate} across ${startDate} to ${endDate}: summarize actual cash drivers so far and upcoming forecast risks.`;
    }

    return {
      startDate,
      endDate,
      dayCount: slice.length,
      granularity: monthlyMode ? "month" : "day",
      spanUnit: monthlyMode ? "m" : "d",
      mode,
      modeLabel,
      currency,
      startBalance,
      endBalance,
      balanceDelta,
      totalIncome,
      totalExpenses,
      netFlow,
      lowestBalance,
      lowestDate,
      highestBalance,
      highestDate,
      suggestedPrompt,
    };
  }

  // Focus Card Listeners
  if (chartFocusCard) {
    chartFocusCard.addEventListener("click", (e) => {
      e.stopPropagation();
      if (e.target.closest("[data-focus-close]")) {
        clearFocus(true);
        return;
      }
      toggleFocusCardExpanded();
    });
    chartFocusCard.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        clearFocus(true);
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        toggleFocusCardExpanded();
      }
    });
  }

  // Period Action Bar Listeners
  if (btnAnalyzePeriod) {
    btnAnalyzePeriod.addEventListener("click", (e) => {
      e.stopPropagation();
      const payload = buildSelectedPeriodPayload();
      if (payload && typeof onAnalyzePeriod === "function") {
        onAnalyzePeriod(payload);
      }
    });
  }

  if (btnClearPeriod) {
    btnClearPeriod.addEventListener("click", (e) => {
      e.stopPropagation();
      clearSelectedPeriod(true);
    });
  }

  // Zoom Buttons (optional)
  if (btnZoomIn) btnZoomIn.addEventListener("click", () => applyZoom(0.65, 0.5));
  if (btnZoomOut) btnZoomOut.addEventListener("click", () => applyZoom(1.5, 0.5));
  if (btnZoomReset) {
    btnZoomReset.addEventListener("click", () => {
      if (!chartHoverMeta) return;
      selectedFocusDate = "";
      focusCardExpanded = false;
      selectedPeriod = null;
      if (chartFocusCard) chartFocusCard.classList.add("hidden");
      if (periodActionBarWrap) periodActionBarWrap.classList.remove("open");
      chartZoom = {
        key: chartZoom.key,
        start: chartHoverMeta.defaultStart,
        end: chartHoverMeta.defaultEnd,
      };
      render(getCurrentState());
    });
  }

  // Mini-Map Drag & Resize Logic
  function startMiniMapDrag(mode, clientX, e) {
    if (!chartHoverMeta || !minimapTrack) return;
    e.stopPropagation();
    e.preventDefault();
    minimapDrag = {
      mode,
      startX: clientX,
      trackWidth: Math.max(1, minimapTrack.getBoundingClientRect().width),
      origStart: chartZoom.start,
      origEnd: chartZoom.end,
      total: chartHoverMeta.allPoints.length,
    };
    if (minimapWindow) minimapWindow.classList.add("dragging");
    hideHover();
  }

  if (minimapHandleLeft) {
    minimapHandleLeft.addEventListener("mousedown", (e) => startMiniMapDrag("left", e.clientX, e));
  }
  if (minimapHandleRight) {
    minimapHandleRight.addEventListener("mousedown", (e) => startMiniMapDrag("right", e.clientX, e));
  }
  if (minimapWindow) {
    minimapWindow.addEventListener("mousedown", (e) => {
      if (e.target.closest(".minimap-handle")) return;
      startMiniMapDrag("pan", e.clientX, e);
    });
  }
  if (minimapTrack) {
    minimapTrack.addEventListener("mousedown", (e) => {
      if (e.target.closest("#minimap-window") || !chartHoverMeta) return;
      const rect = minimapTrack.getBoundingClientRect();
      const clickRatio = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
      const total = chartHoverMeta.allPoints.length;
      const span = chartZoom.end - chartZoom.start + 1;
      const targetCenter = Math.round(clickRatio * (total - 1));
      const newStart = Math.max(0, Math.min(total - span, Math.round(targetCenter - span / 2)));
      chartZoom.start = newStart;
      chartZoom.end = newStart + span - 1;
      render(getCurrentState());
      startMiniMapDrag("pan", e.clientX, e);
    });
  }

  // Main Chart Wheel, Ctrl+LMB Period Drag, Pan & Click-to-Pin (NO auto-zoom)
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
    if (e.button === 0 && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      e.stopPropagation();
      const anchorDate = getDateFromChartClientX(e.clientX);
      if (!anchorDate) return;
      selectedFocusDate = "";
      focusCardExpanded = false;
      hideHover();
      periodDrag = { anchorDate };
      selectedPeriod = { startDate: anchorDate, endDate: anchorDate };
      forecastChart.classList.add("period-selecting");
      render(getCurrentState());
      return;
    }

    chartDrag = {
      startX: e.clientX,
      origStart: chartZoom.start,
      origEnd: chartZoom.end,
      moved: false,
    };
  });

  window.addEventListener("mousemove", (e) => {
    if (periodDrag && chartHoverMeta) {
      const curDate = getDateFromChartClientX(e.clientX);
      if (curDate) {
        const startDate = curDate < periodDrag.anchorDate ? curDate : periodDrag.anchorDate;
        const endDate = curDate < periodDrag.anchorDate ? periodDrag.anchorDate : curDate;
        if (!selectedPeriod || selectedPeriod.startDate !== startDate || selectedPeriod.endDate !== endDate) {
          selectedPeriod = { startDate, endDate };
          render(getCurrentState());
        }
      }
      return;
    }

    if (minimapDrag && chartHoverMeta) {
      const dx = e.clientX - minimapDrag.startX;
      const deltaPts = Math.round((dx / minimapDrag.trackWidth) * (minimapDrag.total - 1));
      const minSpan = Math.min(4, Math.max(1, minimapDrag.total - 1));
      let newStart = chartZoom.start;
      let newEnd = chartZoom.end;

      if (minimapDrag.mode === "left") {
        newStart = Math.max(0, Math.min(minimapDrag.origEnd - minSpan, minimapDrag.origStart + deltaPts));
      } else if (minimapDrag.mode === "right") {
        newEnd = Math.min(
          minimapDrag.total - 1,
          Math.max(minimapDrag.origStart + minSpan, minimapDrag.origEnd + deltaPts)
        );
      } else {
        const span = minimapDrag.origEnd - minimapDrag.origStart;
        newStart = Math.max(0, Math.min(minimapDrag.total - 1 - span, minimapDrag.origStart + deltaPts));
        newEnd = newStart + span;
      }

      if (newStart !== chartZoom.start || newEnd !== chartZoom.end) {
        chartZoom.start = newStart;
        chartZoom.end = newEnd;
        render(getCurrentState());
      }
      return;
    }

    if (!chartDrag || !chartHoverMeta) return;
    const dx = e.clientX - chartDrag.startX;
    if (!chartDrag.moved && Math.abs(dx) < 5) return;
    if (!chartDrag.moved) {
      chartDrag.moved = true;
      forecastChart.classList.add("panning");
      hideHover();
    }
    const span = chartDrag.origEnd - chartDrag.origStart + 1;
    const total = chartHoverMeta.allPoints.length;
    if (span >= total) return;
    const plotArea = document.getElementById("chart-plot-area");
    const plotWidthPx = plotArea ? plotArea.getBoundingClientRect().width : 700;
    const shiftPts = Math.round((-dx / Math.max(1, plotWidthPx)) * span);
    const newStart = Math.max(0, Math.min(total - span, chartDrag.origStart + shiftPts));
    if (newStart !== chartZoom.start) {
      chartZoom.start = newStart;
      chartZoom.end = newStart + span - 1;
      render(getCurrentState());
    }
  });

  window.addEventListener("mouseup", (e) => {
    if (periodDrag) {
      periodDrag = null;
      if (!e.ctrlKey && !e.metaKey) {
        forecastChart.classList.remove("period-selecting");
      }
      const state = getCurrentState();
      if (state && state.has_data) render(state);
      return;
    }

    if (minimapDrag) {
      minimapDrag = null;
      if (minimapWindow) minimapWindow.classList.remove("dragging");
      return;
    }

    if (!chartDrag) return;
    const wasMoved = chartDrag.moved;
    chartDrag = null;
    forecastChart.classList.remove("panning");

    if (!wasMoved && forecastChart.contains(e.target) && chartHoverMeta && chartHoverMeta.visPoints.length) {
      const clickedDate = getDateFromChartClientX(e.clientX);
      if (clickedDate) {
        hideHover();
        if (selectedFocusDate === clickedDate) {
          toggleFocusCardExpanded();
        } else {
          selectedFocusDate = clickedDate;
          focusCardExpanded = false;
          selectedPeriod = null;
          render(getCurrentState());
        }
      }
      return;
    }

    if (forecastChart.contains(e.target)) {
      handleHover(e.clientX);
    } else {
      hideHover();
    }
  });

  window.addEventListener("keydown", (e) => {
    if ((e.key === "Control" || e.key === "Meta") && forecastChart) {
      forecastChart.classList.add("period-selecting");
    } else if (e.key === "Escape") {
      if (selectedPeriod) clearSelectedPeriod(true);
      if (selectedFocusDate) clearFocus(true);
    }
  });

  window.addEventListener("keyup", (e) => {
    if ((e.key === "Control" || e.key === "Meta") && !periodDrag && forecastChart) {
      forecastChart.classList.remove("period-selecting");
    }
  });

  window.addEventListener("blur", () => {
    if (!periodDrag && forecastChart) {
      forecastChart.classList.remove("period-selecting");
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

  return { render, resetZoom, resetAll, clearSelectedPeriod };
}
