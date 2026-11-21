/** Interactive SVG balance & forecast chart with dual-horizon pills, Mini-Map, date pin + animated flow drawer, and Ctrl+LMB period selection. */
import { escapeHtml, fmtMoney, fmtZero } from "./format.js";

const WHATIF_COLOR = "#2f6fdf";
const HIST_PRESET_LABELS = { 1: "1D", 7: "7D", 30: "1M", 90: "3M", 180: "6M", 365: "1Y" };
const FORE_PRESET_LABELS = { 0: "Off", 1: "+1D", 7: "+7D", 30: "+1M", 90: "+3M", 180: "+6M", 365: "+1Y" };

function isMonthlyEngine(state) {
  return Boolean(state && state.liquidity_agent && state.liquidity_agent.granularity === "month");
}

function daysToMonthUnits(days, allowZero = false) {
  const d = Math.max(0, Number(days || 0));
  if (d === 0) return allowZero ? 0 : 1;
  if (d <= 30) return 1;
  if (d <= 90) return 3;
  if (d <= 180) return 6;
  return 12;
}

function parseDateMs(str) {
  if (!str) return 0;
  const clean = str.length === 7 ? `${str}-01` : str.slice(0, 10);
  return Date.parse(clean);
}

function toDateInputValue(str, isMax = false) {
  if (!str) return "";
  if (str.length === 7) return isMax ? `${str}-28` : `${str}-01`;
  return str.slice(0, 10);
}

export function buildFullTimeline(state, whatIf = null) {
  const monthlyMode = isMonthlyEngine(state);
  const la = (state && state.liquidity_agent) || {};
  const hist = monthlyMode
    ? (state && state.monthly_history_series) || []
    : (state && state.history_series) || [];
  const fore = la.full_forecast_series || la.forecast_series || [];

  const dailyPoints = [];
  hist.forEach((p, idx) => {
    dailyPoints.push({
      date: p.date,
      fullDate: p.date,
      bal: Number(p.balance || 0),
      balPess: null,
      balCredit: Number(p.balance || 0),
      balWhatIf: null,
      income: Number(p.income || 0),
      expenses: Number(p.expenses || 0),
      expectedIn: null,
      expectedOut: null,
      kind: "hist",
      dayIdx: idx,
    });
  });

  const baseIdx = dailyPoints.length;
  fore.forEach((p, idx) => {
    dailyPoints.push({
      date: p.date,
      fullDate: p.date,
      bal: Number(p.balance || 0),
      balPess: p.balance_pessimistic != null ? Number(p.balance_pessimistic) : null,
      balCredit: p.balance_with_credit != null ? Number(p.balance_with_credit) : Number(p.balance || 0),
      balWhatIf: whatIf && whatIf[idx] != null ? Number(whatIf[idx]) : null,
      income: Number(p.expected_in ?? p.income ?? 0),
      expenses: Number(p.expected_out ?? p.expenses ?? 0),
      expectedIn: p.expected_in != null ? Number(p.expected_in) : null,
      expectedOut: p.expected_out != null ? Number(p.expected_out) : null,
      kind: "fore",
      dayIdx: baseIdx + idx,
    });
  });

  return {
    dailyPoints,
    todayDailyIdx: Math.max(0, hist.length - 1),
    histCount: hist.length,
    foreCount: fore.length,
    isMonthly: monthlyMode,
  };
}

export function createChartController(
  els,
  { getCurrentState, onHorizonChange, onAnalyzePeriod, getWhatIf = () => null }
) {
  const {
    forecastPanel,
    forecastChart,
    chartTooltip,
    chartGranularityBadge,
    chartRangeLabel,
    chartLegend,
    historyPillGroup,
    forecastPillGroup,
    dateJumpBar,
    chartFocusDateInput,
    chartFocusClearBtn,
    chartFocusCard,
    minimapTrack,
    minimapSvg,
    minimapMaskLeft,
    minimapMaskRight,
    minimapWindow,
    minimapHandleLeft,
    minimapHandleRight,
    minimapHorizonSummary,
    periodActionBarWrap,
    periodRangeDatesEl,
    btnAnalyzePeriod,
    btnClearPeriod,
  } = els;

  let currentGranularity = null;
  let chartHoverMeta = null;
  let selectedHistDays = 90;
  let selectedForeDays = 30;
  let selectedFocusDate = "";
  let focusCardExpanded = false;
  let selectedPeriod = null; // { startDate, endDate }
  let periodDrag = null; // { anchorDate }
  let chartZoom = { key: null, start: 0, end: 0, custom: false };
  let chartDrag = null;
  let minimapDrag = null;

  function computePresetWindow(todayDailyIdx, totalDaily, histDays, foreDays, monthlyMode = false) {
    const hUnits = monthlyMode
      ? daysToMonthUnits(histDays, false)
      : Math.max(1, Number(histDays || 90));
    const fUnits = monthlyMode
      ? daysToMonthUnits(foreDays, true)
      : Math.max(0, Number(foreDays ?? 30));
    const start = Math.max(0, todayDailyIdx - hUnits);
    const end = Math.max(start, Math.min(totalDaily - 1, todayDailyIdx + fUnits));
    return { start, end };
  }

  function buildAutoGranularPoints(dailyPoints, visStartDayIdx, visEndDayIdx, todayDailyIdx, monthlyMode, state) {
    const spanDays = Math.max(1, visEndDayIdx - visStartDayIdx);
    if (monthlyMode) {
      const points = dailyPoints.slice(visStartDayIdx, visEndDayIdx + 1).map((p, idx) => ({
        date: p.date,
        axisDate: p.date,
        bal: p.bal,
        balPess: p.balPess,
        balCredit: p.balCredit,
        income: p.income,
        expenses: p.expenses,
        expectedIn: p.expectedIn,
        expectedOut: p.expectedOut,
        kind: p.kind,
        isTodayBoundary: visStartDayIdx + idx === todayDailyIdx,
      }));
      return { points, granularity: "monthly", spanDays };
    }

    let granularity = "daily";
    if (spanDays <= 7) {
      granularity = "hourly";
    } else if (spanDays > 120) {
      granularity = "weekly";
    }

    if (granularity === "hourly") {
      const hours = ["04:00", "08:00", "12:00", "16:00", "20:00", "24:00"];
      const profileWeights = [0.12, 0.28, 0.52, 0.74, 0.91, 1.0];
      const points = [];
      const firstDay = dailyPoints[visStartDayIdx];
      if (firstDay) {
        const prevBal0 =
          visStartDayIdx > 0
            ? dailyPoints[visStartDayIdx - 1].bal
            : Number((state && state.opening_balance) ?? firstDay.bal);
        points.push({
          date: `${firstDay.date} 00:00`,
          axisDate: firstDay.date,
          bal: Number(prevBal0.toFixed(2)),
          balPess: null,
          balCredit: Number(prevBal0.toFixed(2)),
          kind: firstDay.kind,
          isTodayBoundary: visStartDayIdx === todayDailyIdx && visEndDayIdx === todayDailyIdx,
        });
      }

      const loopStart = visEndDayIdx > visStartDayIdx ? visStartDayIdx + 1 : visStartDayIdx;
      for (let d = loopStart; d <= visEndDayIdx; d++) {
        const cur = dailyPoints[d];
        const prevBal =
          d > 0
            ? dailyPoints[d - 1].bal
            : Number((state && state.opening_balance) ?? cur.bal);
        const prevCred =
          d > 0
            ? dailyPoints[d - 1].balCredit ?? dailyPoints[d - 1].bal
            : prevBal;
        const dBal = cur.bal - prevBal;
        const dCred = (cur.balCredit ?? cur.bal) - prevCred;
        hours.forEach((hh, hIdx) => {
          const w = profileWeights[hIdx];
          points.push({
            date: `${cur.date} ${hh}`,
            axisDate: cur.date,
            bal: Number((prevBal + dBal * w).toFixed(2)),
            balPess: null,
            balCredit: Number((prevCred + dCred * w).toFixed(2)),
            kind: cur.kind,
            isTodayBoundary: d === todayDailyIdx && hIdx === hours.length - 1,
          });
        });
      }
      return { points, granularity, spanDays };
    }

    if (granularity === "weekly") {
      const points = [];
      const histEnd = Math.min(visEndDayIdx, todayDailyIdx);
      if (visStartDayIdx <= histEnd) {
        const histIndices = [];
        for (let d = histEnd; d >= visStartDayIdx; d -= 7) {
          histIndices.push(d);
        }
        if (histIndices[histIndices.length - 1] !== visStartDayIdx) {
          histIndices.push(visStartDayIdx);
        }
        histIndices.reverse().forEach((d) => {
          const p = dailyPoints[d];
          points.push({
            date: p.date,
            axisDate: p.date,
            bal: p.bal,
            balPess: null,
            balCredit: p.balCredit,
            kind: "hist",
            isTodayBoundary: d === todayDailyIdx,
          });
        });
      }

      const foreStart = Math.max(visStartDayIdx, todayDailyIdx + 1);
      if (foreStart <= visEndDayIdx) {
        for (let d = foreStart; d <= visEndDayIdx; d += 7) {
          const idx = Math.min(visEndDayIdx, d + 6);
          const p = dailyPoints[idx];
          points.push({
            date: p.date,
            axisDate: p.date,
            bal: p.bal,
            balPess: null,
            balCredit: p.balCredit,
            balWhatIf: p.balWhatIf ?? null,
            kind: "fore",
            isTodayBoundary: false,
          });
          if (idx === visEndDayIdx) break;
        }
      }
      return { points, granularity, spanDays };
    }

    const points = dailyPoints.slice(visStartDayIdx, visEndDayIdx + 1).map((p, idx) => ({
      date: p.date,
      axisDate: p.date,
      bal: p.bal,
      balPess: p.balPess,
      balCredit: p.balCredit,
      balWhatIf: p.balWhatIf ?? null,
      income: p.income,
      expenses: p.expenses,
      expectedIn: p.expectedIn,
      expectedOut: p.expectedOut,
      kind: p.kind,
      isTodayBoundary: visStartDayIdx + idx === todayDailyIdx,
    }));
    return { points, granularity, spanDays };
  }

  function syncGranularity(la) {
    const newGran = la && la.granularity === "month" ? "month" : "day";
    if (currentGranularity !== newGran) {
      currentGranularity = newGran;
      if (newGran === "month") {
        selectedHistDays = 365;
        selectedForeDays =
          la.horizon_months === 0
            ? 0
            : la.horizon_months <= 1
            ? 30
            : la.horizon_months <= 3
            ? 90
            : la.horizon_months <= 6
            ? 180
            : 365;
      } else {
        selectedHistDays = 90;
        selectedForeDays = Math.max(0, Number(la && la.horizon_days != null ? la.horizon_days : 30));
      }
    }
  }

  function getHorizonPayload() {
    if (currentGranularity === "month") {
      return { horizon_months: daysToMonthUnits(selectedForeDays, true) };
    }
    if (currentGranularity === "day") {
      return { horizon_days: Math.max(0, Number(selectedForeDays ?? 30)) };
    }
    return {};
  }

  function resetZoom() {
    chartZoom = { key: null, start: 0, end: 0, custom: false };
    chartDrag = null;
    minimapDrag = null;
  }

  function resetAll() {
    currentGranularity = null;
    selectedHistDays = 90;
    selectedForeDays = 30;
    selectedFocusDate = "";
    focusCardExpanded = false;
    selectedPeriod = null;
    periodDrag = null;
    resetZoom();
    if (chartFocusDateInput) chartFocusDateInput.value = "";
    if (chartFocusCard) chartFocusCard.classList.add("hidden");
    if (periodActionBarWrap) periodActionBarWrap.classList.remove("open");
    if (forecastChart) forecastChart.classList.remove("period-selecting");
  }

  function syncHorizonPillStates(todayDailyIdx, totalDaily, visStart, visEnd, monthlyMode = false) {
    const actualHistSpan = Math.max(0, todayDailyIdx - visStart);
    const actualForeSpan = Math.max(0, visEnd - todayDailyIdx);
    const unit = monthlyMode ? "m" : "d";

    let matchedHist = null;
    if (historyPillGroup) {
      historyPillGroup.querySelectorAll(".hist-pill").forEach((btn) => {
        const d = Number(btn.getAttribute("data-hist-days"));
        const units = monthlyMode ? daysToMonthUnits(d, false) : d;
        const expectedStart = Math.max(0, todayDailyIdx - units);
        const isMatch = !chartZoom.custom && d === selectedHistDays && visStart === expectedStart;
        btn.classList.toggle("active", isMatch);
        if (isMatch) matchedHist = HIST_PRESET_LABELS[d] || `${d}D`;
      });
    }

    let matchedFore = null;
    if (forecastPillGroup) {
      forecastPillGroup.querySelectorAll(".fore-pill").forEach((btn) => {
        const d = Number(btn.getAttribute("data-fore-days"));
        const units = monthlyMode ? daysToMonthUnits(d, true) : d;
        const expectedEnd = Math.min(totalDaily - 1, todayDailyIdx + units);
        const isMatch = !chartZoom.custom && d === selectedForeDays && visEnd === expectedEnd;
        btn.classList.toggle("active", isMatch);
        if (isMatch) matchedFore = FORE_PRESET_LABELS[d] || `+${d}D`;
      });
    }

    if (minimapHorizonSummary) {
      if (selectedFocusDate) {
        minimapHorizonSummary.textContent = `Pinned Date: ${selectedFocusDate} (${actualHistSpan}${unit} past · +${actualForeSpan}${unit} forecast)`;
      } else if (matchedHist && matchedFore) {
        minimapHorizonSummary.textContent = `Past: ${matchedHist} · Forecast: ${matchedFore}`;
      } else {
        minimapHorizonSummary.textContent = `Custom Window (${actualHistSpan}${unit} past · +${actualForeSpan}${unit} forecast)`;
      }
    }
  }

  function renderMiniMap(dailyPoints, todayDailyIdx, visStart, visEnd, hasApprovedCredit) {
    const total = dailyPoints.length;
    if (!minimapSvg || !minimapTrack || total === 0) return;

    const MW = Math.max(480, Math.round(minimapTrack.clientWidth || 900));
    const MH = 42;
    minimapSvg.setAttribute("viewBox", `0 0 ${MW} ${MH}`);

    const vals = [0];
    dailyPoints.forEach((p) => {
      vals.push(p.bal);
      if (hasApprovedCredit && p.kind === "fore" && p.balCredit != null) {
        vals.push(p.balCredit);
      }
    });
    let sMin = Math.min(...vals);
    let sMax = Math.max(...vals);
    if (sMin === sMax) {
      sMin -= 1000;
      sMax += 1000;
    }

    const xForMini = (idx) => (total <= 1 ? 0 : (idx / (total - 1)) * MW);
    const yForMini = (v) => 5 + ((sMax - v) / (sMax - sMin)) * (MH - 10);
    const baseBottom = MH - 2;

    const histMiniPts = [];
    const foreMiniPts = [];
    dailyPoints.forEach((p, i) => {
      const coord = `${xForMini(i).toFixed(1)},${yForMini(p.bal).toFixed(1)}`;
      if (i <= todayDailyIdx) histMiniPts.push(coord);
      if (i >= todayDailyIdx) foreMiniPts.push(coord);
    });

    const todayX = xForMini(todayDailyIdx);
    const focusDailyIdx = selectedFocusDate
      ? dailyPoints.findIndex((p) => p.date === selectedFocusDate)
      : -1;
    const focusMiniX = focusDailyIdx >= 0 ? xForMini(focusDailyIdx) : -1;
    const focusMiniY = focusDailyIdx >= 0 ? yForMini(dailyPoints[focusDailyIdx].bal) : -1;

    let periodMiniHtml = "";
    if (selectedPeriod && selectedPeriod.startDate && selectedPeriod.endDate) {
      const pStartIdx = dailyPoints.findIndex((p) => p.date >= selectedPeriod.startDate);
      let pEndIdx = -1;
      for (let i = dailyPoints.length - 1; i >= 0; i--) {
        if (dailyPoints[i].date <= selectedPeriod.endDate) {
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
        )}" height="${MH}" fill="rgba(34, 197, 94, 0.24)" stroke="#16a34a" stroke-width="1" />`;
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
      <line x1="${todayX.toFixed(1)}" y1="0" x2="${todayX.toFixed(1)}" y2="${MH}" stroke="#623761" stroke-dasharray="2 2" stroke-width="1.5" />
      <text x="${Math.min(MW - 22, Math.max(22, todayX + 4)).toFixed(1)}" y="11" font-size="9" font-weight="700" fill="#623761">Today</text>
      ${
        focusDailyIdx >= 0
          ? `<line x1="${focusMiniX.toFixed(1)}" y1="0" x2="${focusMiniX.toFixed(1)}" y2="${MH}" stroke="#d97706" stroke-width="1.8" />
             <circle cx="${focusMiniX.toFixed(1)}" cy="${focusMiniY.toFixed(1)}" r="3.2" fill="#d97706" stroke="#ffffff" stroke-width="1.2" />`
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
    if (!state) return;
    const currency = (state && state.currency) || "EUR";
    chartTooltip.classList.add("hidden");

    const whatIf = getWhatIf(state);
    const hasWhatIf = Boolean(whatIf);
    const { dailyPoints, todayDailyIdx, histCount, foreCount, isMonthly } = buildFullTimeline(
      state,
      whatIf
    );
    const totalDaily = dailyPoints.length;

    if (totalDaily === 0) {
      chartHoverMeta = null;
      forecastChart.innerHTML = "";
      if (minimapSvg) minimapSvg.innerHTML = "";
      if (chartRangeLabel) chartRangeLabel.textContent = "—";
      if (chartLegend) chartLegend.classList.add("hidden");
      if (chartFocusCard) chartFocusCard.classList.add("hidden");
      if (periodActionBarWrap) periodActionBarWrap.classList.remove("open");
      return;
    }

    if (chartFocusDateInput) {
      chartFocusDateInput.min = toDateInputValue(dailyPoints[0].date, false);
      chartFocusDateInput.max = toDateInputValue(dailyPoints[totalDaily - 1].date, true);
      const focusInputVal = toDateInputValue(selectedFocusDate, false);
      if (
        document.activeElement !== chartFocusDateInput &&
        chartFocusDateInput.value !== focusInputVal
      ) {
        chartFocusDateInput.value = focusInputVal;
      }
    }
    if (dateJumpBar) {
      dateJumpBar.classList.toggle("has-focus", Boolean(selectedFocusDate));
    }
    if (chartFocusClearBtn) {
      chartFocusClearBtn.classList.toggle("hidden", !selectedFocusDate);
    }

    const hasApprovedCredit =
      state && state.approved_credit && Number(state.approved_credit.amount) > 0;

    const defaultPreset = computePresetWindow(
      todayDailyIdx,
      totalDaily,
      isMonthly ? 365 : 90,
      isMonthly ? 365 : 30,
      isMonthly
    );
    const activePreset = computePresetWindow(
      todayDailyIdx,
      totalDaily,
      selectedHistDays,
      selectedForeDays,
      isMonthly
    );

    const zoomKey = `${isMonthly ? "month" : "day"}:${histCount}:${foreCount}:${totalDaily}`;
    if (chartZoom.key !== zoomKey) {
      chartZoom = {
        key: zoomKey,
        start: activePreset.start,
        end: activePreset.end,
        custom: false,
      };
    }

    const visStart = Math.max(0, Math.min(totalDaily - 1, chartZoom.start));
    const visEnd = Math.max(visStart, Math.min(totalDaily - 1, chartZoom.end));
    chartZoom.start = visStart;
    chartZoom.end = visEnd;

    const { points: visPoints, granularity, spanDays } = buildAutoGranularPoints(
      dailyPoints,
      visStart,
      visEnd,
      todayDailyIdx,
      isMonthly,
      state
    );

    const granLabel =
      granularity === "monthly"
        ? `Monthly Seasonal (${spanDays}m span)`
        : granularity === "hourly"
        ? `Auto: Hourly (${spanDays}d span)`
        : granularity === "weekly"
        ? `Auto: Weekly (${spanDays}d span)`
        : `Auto: Daily (${spanDays}d span)`;
    if (chartGranularityBadge) {
      chartGranularityBadge.textContent = granLabel;
    }

    syncHorizonPillStates(todayDailyIdx, totalDaily, visStart, visEnd, isMonthly);
    renderMiniMap(dailyPoints, todayDailyIdx, visStart, visEnd, hasApprovedCredit);

    const firstDate = visPoints[0].axisDate || visPoints[0].date;
    const lastDate = visPoints[visPoints.length - 1].axisDate || visPoints[visPoints.length - 1].date;
    if (chartRangeLabel) {
      chartRangeLabel.textContent = `${firstDate} → ${lastDate}`;
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
    const rightPad = 24;
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

    let splitLocalIdx = -1;
    for (let i = visPoints.length - 1; i >= 0; i--) {
      if (visPoints[i].kind === "hist") {
        splitLocalIdx = i;
        break;
      }
    }
    const showToday = splitLocalIdx >= 0 && splitLocalIdx < visPoints.length;
    const splitX = showToday ? xFor(splitLocalIdx) : leftPad;

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

    const whatIfLegend = `<span class="legend-item"><span class="swatch" style="background: ${WHATIF_COLOR}"></span>What-if scenario</span>`;
    if (chartLegend) {
      if (isMonthly) {
        const items = [
          '<span class="legend-item"><span class="swatch" style="background: #0d0d0d"></span>Past</span>',
          '<span class="legend-item"><span class="swatch" style="background: #805181"></span>Expected forecast</span>',
          '<span class="legend-item"><span class="swatch" style="background: #c96b7c"></span>Pessimistic stress test</span>',
        ];
        if (hasApprovedCredit) {
          items.push(
            '<span class="legend-item"><span class="swatch" style="background: #24664e"></span>With approved credit</span>'
          );
        }
        if (hasWhatIf) items.push(whatIfLegend);
        chartLegend.innerHTML = items.join("");
        chartLegend.classList.remove("hidden");
      } else if (hasApprovedCredit || hasWhatIf) {
        const items = [
          '<span class="legend-item"><span class="swatch" style="background: #805181"></span>Forecast balance</span>',
        ];
        if (hasApprovedCredit) {
          items.push(
            '<span class="legend-item"><span class="swatch" style="background: #24664e"></span>With approved credit</span>'
          );
        }
        if (hasWhatIf) items.push(whatIfLegend);
        chartLegend.innerHTML = items.join("");
        chartLegend.classList.remove("hidden");
      } else {
        chartLegend.classList.add("hidden");
      }
    }

    const showMinLabel = Math.abs(minY - maxY) >= 14;
    const showZeroLabel =
      Math.abs(zeroY - maxY) >= 14 && (!showMinLabel || Math.abs(zeroY - minY) >= 14);
    const clampLabelY = (y) => Math.max(topPad + 6, Math.min(H - bottomPad + 4, y + 4));

    // Pinned Date Marker & Animated Expandable Income/Expenses/Total Panel
    let focusMarkerHtml = "";
    let showFocusCard = false;
    if (selectedFocusDate) {
      let focusLocalIdx = -1;
      for (let i = visPoints.length - 1; i >= 0; i--) {
        const pDate = visPoints[i].axisDate || visPoints[i].date.slice(0, 10);
        if (pDate === selectedFocusDate) {
          focusLocalIdx = i;
          break;
        }
      }
      if (focusLocalIdx < 0 && visPoints.length > 0) {
        const fStart = visPoints[0].axisDate || visPoints[0].date.slice(0, 10);
        const fEnd =
          visPoints[visPoints.length - 1].axisDate ||
          visPoints[visPoints.length - 1].date.slice(0, 10);
        if (selectedFocusDate >= fStart && selectedFocusDate <= fEnd) {
          let bestIdx = 0;
          let bestDiff = Infinity;
          const targetMs = parseDateMs(selectedFocusDate);
          visPoints.forEach((p, idx) => {
            const ms = parseDateMs(p.axisDate || p.date.slice(0, 10));
            const diff = Math.abs(ms - targetMs);
            if (diff < bestDiff) {
              bestDiff = diff;
              bestIdx = idx;
            }
          });
          focusLocalIdx = bestIdx;
        }
      }

      if (focusLocalIdx >= 0) {
        showFocusCard = true;
        const fPt = visPoints[focusLocalIdx];
        const exactDay = dailyPoints.find((d) => d.date === selectedFocusDate) || fPt;
        const dayIncome = Number(exactDay.income || 0);
        const dayExpenses = Number(exactDay.expenses || 0);
        const totalFlow = dayIncome + dayExpenses;
        const incPct = totalFlow > 0 ? Math.round((dayIncome / totalFlow) * 100) : 50;
        const expPct = totalFlow > 0 ? 100 - incPct : 50;

        const fx = xFor(focusLocalIdx);
        const fy = yFor(fPt.bal);
        const fKind = fPt.kind === "hist" ? "Past" : "Forecast";
        const fBalText = fmtMoney(fPt.bal, currency);
        const fIncText = `+${fmtMoney(dayIncome, currency)}`;
        const fExpText = `-${fmtMoney(dayExpenses, currency)}`;
        const dayNet = Number((dayIncome - dayExpenses).toFixed(2));
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
          )}" y2="${H - bottomPad}" stroke="#d97706" stroke-dasharray="4 3" stroke-width="1.8" />
          <circle cx="${fx.toFixed(1)}" cy="${fy.toFixed(1)}" r="9" fill="rgba(217, 119, 6, 0.22)" />
          <circle id="chart-focus-dot" cx="${fx.toFixed(1)}" cy="${fy.toFixed(
            1
          )}" r="5.5" fill="#d97706" stroke="#ffffff" stroke-width="2" style="cursor: pointer" />
        `;

        if (chartFocusCard) {
          const clampedX = Math.max(leftPad + 86, Math.min(W - rightPad - 86, fx));
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
              <span class="focus-card-chevron" aria-hidden="true">▾</span>
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

    // Ctrl + LMB Selected Period Highlight (super-light-green band + dark-green boundary dots)
    let periodBandBackHtml = "";
    let periodDotsFrontHtml = "";
    if (selectedPeriod && selectedPeriod.startDate && selectedPeriod.endDate && visPoints.length > 0) {
      const pStart = selectedPeriod.startDate;
      const pEnd = selectedPeriod.endDate;
      const visFirstDate = visPoints[0].axisDate || visPoints[0].date.slice(0, 10);
      const visLastDate =
        visPoints[visPoints.length - 1].axisDate || visPoints[visPoints.length - 1].date.slice(0, 10);

      if (pEnd >= visFirstDate && pStart <= visLastDate) {
        const findClosestVisIdx = (targetDate, preferLast = false) => {
          let exactIdx = -1;
          if (preferLast) {
            for (let i = visPoints.length - 1; i >= 0; i--) {
              if ((visPoints[i].axisDate || visPoints[i].date.slice(0, 10)) === targetDate) {
                exactIdx = i;
                break;
              }
            }
          } else {
            for (let i = 0; i < visPoints.length; i++) {
              if ((visPoints[i].axisDate || visPoints[i].date.slice(0, 10)) === targetDate) {
                exactIdx = i;
                break;
              }
            }
          }
          if (exactIdx >= 0) return exactIdx;
          const targetMs = parseDateMs(targetDate);
          let bestIdx = 0;
          let bestDiff = Infinity;
          visPoints.forEach((p, idx) => {
            const diff = Math.abs(parseDateMs(p.axisDate || p.date.slice(0, 10)) - targetMs);
            if (diff < bestDiff) {
              bestDiff = diff;
              bestIdx = idx;
            }
          });
          return bestIdx;
        };

        const sVisIdx = pStart <= visFirstDate ? 0 : findClosestVisIdx(pStart, false);
        const eVisIdx = pEnd >= visLastDate ? visPoints.length - 1 : findClosestVisIdx(pEnd, true);
        const loIdx = Math.min(sVisIdx, eVisIdx);
        const hiIdx = Math.max(sVisIdx, eVisIdx);
        const rawX1 = xFor(loIdx);
        const rawX2 = xFor(hiIdx);
        const bandW = Math.max(8, rawX2 - rawX1);
        const bandX = rawX2 - rawX1 < 8 ? Math.max(leftPad, rawX1 - 4) : rawX1;
        const showStartDot = pStart >= visFirstDate;
        const showEndDot = pEnd <= visLastDate;

        periodBandBackHtml = `
          <rect id="chart-period-band" x="${bandX.toFixed(1)}" y="${topPad}" width="${bandW.toFixed(
            1
          )}" height="${plotH}" fill="rgba(34, 197, 94, 0.15)" />
          <line x1="${rawX1.toFixed(1)}" y1="${topPad}" x2="${rawX1.toFixed(1)}" y2="${
            H - bottomPad
          }" stroke="#16a34a" stroke-opacity="0.55" stroke-dasharray="3 2" stroke-width="1.3" />
          <line x1="${rawX2.toFixed(1)}" y1="${topPad}" x2="${rawX2.toFixed(1)}" y2="${
            H - bottomPad
          }" stroke="#16a34a" stroke-opacity="0.55" stroke-dasharray="3 2" stroke-width="1.3" />
        `;

        const startDotY = yFor(visPoints[loIdx].bal);
        const endDotY = yFor(visPoints[hiIdx].bal);
        periodDotsFrontHtml = `
          ${
            showStartDot
              ? `<circle id="chart-period-dot-start" cx="${rawX1.toFixed(1)}" cy="${startDotY.toFixed(
                  1
                )}" r="5.5" fill="#15803d" stroke="#ffffff" stroke-width="2" />`
              : ""
          }
          ${
            showEndDot
              ? `<circle id="chart-period-dot-end" cx="${rawX2.toFixed(1)}" cy="${endDotY.toFixed(
                  1
                )}" r="5.5" fill="#15803d" stroke="#ffffff" stroke-width="2" />`
              : ""
          }
        `;
      }
    }

    if (periodActionBarWrap && periodRangeDatesEl) {
      if (selectedPeriod && selectedPeriod.startDate && selectedPeriod.endDate) {
        periodRangeDatesEl.textContent = `${selectedPeriod.startDate} → ${selectedPeriod.endDate}`;
        periodActionBarWrap.classList.add("open");
      } else {
        periodActionBarWrap.classList.remove("open");
      }
    }

    chartHoverMeta = {
      allPoints: dailyPoints,
      todayDailyIdx,
      visPoints,
      visStart,
      visEnd,
      defaultStart: defaultPreset.start,
      defaultEnd: defaultPreset.end,
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
    if (total <= 2) return;
    const curSpan = chartZoom.end - chartZoom.start + 1;
    const minSpan = Math.min(total, 2);
    const newSpan = Math.max(minSpan, Math.min(total, Math.round(curSpan * factor)));
    if (newSpan === curSpan && !(factor > 1 && newSpan === total)) return;

    const anchorIdx = chartZoom.start + Math.round(anchorRatio * (curSpan - 1));
    let newStart = Math.round(anchorIdx - anchorRatio * (newSpan - 1));
    newStart = Math.max(0, Math.min(total - newSpan, newStart));
    chartZoom.start = newStart;
    chartZoom.end = newStart + newSpan - 1;
    chartZoom.custom = true;
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
    const m = chartHoverMeta;
    const ratio = getHoverRatio(clientX);
    const localIdx =
      m.visPoints.length <= 1
        ? 0
        : Math.max(0, Math.min(m.visPoints.length - 1, Math.round(ratio * (m.visPoints.length - 1))));
    const pt = m.visPoints[localIdx];
    return pt.axisDate || pt.date.slice(0, 10);
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
    const ptDateOnly = pt.axisDate || pt.date.slice(0, 10);
    if (selectedFocusDate && ptDateOnly === selectedFocusDate) {
      hideHover();
      return;
    }

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
        ? `<div class="tt-credit">With credit: ${escapeHtml(fmtMoney(pt.balCredit, m.currency))}</div>`
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

  function applyFocusDateValue(rawVal, allowClampAndOverwrite = false) {
    const state = getCurrentState();
    if (!state || !state.has_data || !chartHoverMeta) return;
    const val = (rawVal || "").trim();
    if (!val) {
      if (allowClampAndOverwrite) {
        selectedFocusDate = "";
        focusCardExpanded = false;
        render(state);
      }
      return;
    }

    // Ignore intermediate keystrokes while typing a 4-digit year (e.g. 0002, 0020, 0202)
    const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(val);
    if (!m) return;
    const yearNum = Number(m[1]);
    if (yearNum < 1990 || yearNum > 2100) return;

    const allPts = chartHoverMeta.allPoints;
    const total = allPts.length;
    if (!total) return;

    const monthlyMode = isMonthlyEngine(state);
    const lookupVal = monthlyMode ? val.slice(0, 7) : val;

    let targetIdx = allPts.findIndex((p) => p.date === lookupVal);
    if (targetIdx < 0) {
      if (!allowClampAndOverwrite) return;
      const targetMs = parseDateMs(lookupVal);
      let bestIdx = 0;
      let bestDiff = Infinity;
      allPts.forEach((p, idx) => {
        const diff = Math.abs(parseDateMs(p.date) - targetMs);
        if (diff < bestDiff) {
          bestDiff = diff;
          bestIdx = idx;
        }
      });
      targetIdx = bestIdx;
    }

    selectedFocusDate = allPts[targetIdx].date;
    selectedPeriod = null;
    if (allowClampAndOverwrite && chartFocusDateInput) {
      const inputFormatted = toDateInputValue(selectedFocusDate, false);
      if (chartFocusDateInput.value !== inputFormatted) {
        chartFocusDateInput.value = inputFormatted;
      }
    }

    const halfWindow = monthlyMode ? 3 : 7;
    chartZoom.start = Math.max(0, targetIdx - halfWindow);
    chartZoom.end = Math.min(total - 1, targetIdx + halfWindow);
    if (chartZoom.end === chartZoom.start && total > 1) {
      if (chartZoom.start > 0) chartZoom.start -= 1;
      else chartZoom.end += 1;
    }
    chartZoom.custom = true;
    render(state);
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

  function buildSelectedPeriodPayload() {
    const state = getCurrentState();
    if (!selectedPeriod || !chartHoverMeta || !chartHoverMeta.allPoints.length) return null;
    const allPts = chartHoverMeta.allPoints;
    const todayIdx = chartHoverMeta.todayDailyIdx;
    const currency = chartHoverMeta.currency || "EUR";
    const monthlyMode = isMonthlyEngine(state);

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

    totalIncome = Number(totalIncome.toFixed(2));
    totalExpenses = Number(totalExpenses.toFixed(2));
    const netFlow = Number((totalIncome - totalExpenses).toFixed(2));
    const startBalance = slice[0].bal;
    const endBalance = slice[slice.length - 1].bal;
    const balanceDelta = Number((endBalance - startBalance).toFixed(2));
    const startDate = slice[0].date;
    const endDate = slice[slice.length - 1].date;

    let mode = "past";
    let modeLabel = "Past";
    if (sIdx > todayIdx) {
      mode = "future";
      modeLabel = "Future";
    } else if (eIdx > todayIdx) {
      mode = "mixed";
      modeLabel = "Past & Future";
    }

    const startBalFmt = fmtMoney(startBalance, currency);
    const endBalFmt = fmtMoney(endBalance, currency);
    let suggestedPrompt = "";
    if (mode === "past") {
      suggestedPrompt = `Analyze what happened between ${startDate} and ${endDate}: explain the main income drivers, largest expenses, and why my balance moved from ${startBalFmt} to ${endBalFmt}.`;
    } else if (mode === "future") {
      suggestedPrompt = `Analyze what is most likely going to happen between ${startDate} and ${endDate}: highlight expected upcoming bills, potential cash flow risks, and why my projected balance moves from ${startBalFmt} to ${endBalFmt}.`;
    } else {
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

  // Symmetric Top Horizon Pill Click Handlers
  if (historyPillGroup) {
    historyPillGroup.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-hist-days]");
      const state = getCurrentState();
      if (!btn || !state || !state.has_data || !chartHoverMeta) return;
      selectedHistDays = Number(btn.getAttribute("data-hist-days"));
      selectedFocusDate = "";
      focusCardExpanded = false;
      const total = chartHoverMeta.allPoints.length;
      const todayIdx = chartHoverMeta.todayDailyIdx;
      const w = computePresetWindow(
        todayIdx,
        total,
        selectedHistDays,
        selectedForeDays,
        isMonthlyEngine(state)
      );
      chartZoom.start = w.start;
      chartZoom.end = w.end;
      chartZoom.custom = false;
      render(state);
    });
  }

  if (forecastPillGroup) {
    forecastPillGroup.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-fore-days]");
      const state = getCurrentState();
      if (!btn || !state || !state.has_data || !chartHoverMeta) return;
      selectedForeDays = Number(btn.getAttribute("data-fore-days"));
      selectedFocusDate = "";
      focusCardExpanded = false;
      const total = chartHoverMeta.allPoints.length;
      const todayIdx = chartHoverMeta.todayDailyIdx;
      const w = computePresetWindow(
        todayIdx,
        total,
        selectedHistDays,
        selectedForeDays,
        isMonthlyEngine(state)
      );
      chartZoom.start = w.start;
      chartZoom.end = w.end;
      chartZoom.custom = false;
      render(state);
      if (typeof onHorizonChange === "function") {
        await onHorizonChange();
      }
    });
  }

  // Specific Date Input & Focus Card Listeners
  if (chartFocusDateInput) {
    chartFocusDateInput.addEventListener("input", () => {
      applyFocusDateValue(chartFocusDateInput.value, false);
    });
    chartFocusDateInput.addEventListener("change", () => {
      const isFocused = document.activeElement === chartFocusDateInput;
      applyFocusDateValue(chartFocusDateInput.value, !isFocused);
    });
    chartFocusDateInput.addEventListener("blur", () => {
      if (chartFocusDateInput.value) {
        applyFocusDateValue(chartFocusDateInput.value, true);
      }
    });
    chartFocusDateInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        applyFocusDateValue(chartFocusDateInput.value, true);
        chartFocusDateInput.blur();
      } else if (e.key === "Escape") {
        e.preventDefault();
        if (chartFocusClearBtn) chartFocusClearBtn.click();
        chartFocusDateInput.blur();
      }
    });
  }

  if (chartFocusCard) {
    chartFocusCard.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleFocusCardExpanded();
    });
    chartFocusCard.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        toggleFocusCardExpanded();
      }
    });
  }

  if (chartFocusClearBtn) {
    chartFocusClearBtn.addEventListener("click", () => {
      const state = getCurrentState();
      if (!state || !state.has_data || !chartHoverMeta) return;
      selectedFocusDate = "";
      focusCardExpanded = false;
      if (chartFocusDateInput) chartFocusDateInput.value = "";
      const total = chartHoverMeta.allPoints.length;
      const todayIdx = chartHoverMeta.todayDailyIdx;
      const w = computePresetWindow(
        todayIdx,
        total,
        selectedHistDays,
        selectedForeDays,
        isMonthlyEngine(state)
      );
      chartZoom.start = w.start;
      chartZoom.end = w.end;
      chartZoom.custom = false;
      render(state);
    });
  }

  // Centered Period Action Bar Listeners
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

  // Interactive Mini-Map Drag & Resize Logic
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
      chartZoom.custom = true;
      render(getCurrentState());
      startMiniMapDrag("pan", e.clientX, e);
    });
  }

  // Main Chart Wheel, Ctrl+LMB Period Drag, Pan & Click-to-Lock
  forecastChart.addEventListener(
    "wheel",
    (e) => {
      if (!chartHoverMeta || chartHoverMeta.allPoints.length <= 2) return;
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
          chartZoom.custom = true;
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
      if (chartFocusDateInput) chartFocusDateInput.value = "";
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
      const minSpan = 1;
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
        chartZoom.custom = true;
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
      chartZoom.custom = true;
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
      const m = chartHoverMeta;
      const ratio = getHoverRatio(e.clientX);
      const localIdx =
        m.visPoints.length <= 1
          ? 0
          : Math.max(0, Math.min(m.visPoints.length - 1, Math.round(ratio * (m.visPoints.length - 1))));
      const clickedPt = m.visPoints[localIdx];
      const clickedDate = clickedPt.axisDate || clickedPt.date.slice(0, 10);
      hideHover();
      if (selectedFocusDate === clickedDate) {
        toggleFocusCardExpanded();
      } else {
        applyFocusDateValue(clickedDate, true);
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
    } else if (e.key === "Escape" && selectedPeriod) {
      clearSelectedPeriod(true);
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

  return {
    render,
    resetZoom,
    resetAll,
    syncGranularity,
    getHorizonPayload,
    clearSelectedPeriod,
  };
}
