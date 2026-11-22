/** What-if scenarios: re-project the forecast balance with simple user levers (no backend, no LLM). */
import { escapeHtml, fmtMoney, fmtZero } from "./format.js";
import { animateHeight } from "./controls.js";

const PRESET_LEVERS = { income: "incomeInput", expense: "expenseInput", monthly: "monthlyInput" };

/**
 * Return what-if balances aligned with `forecastSeries`, or null when no lever is active.
 * Each point's change in cash is shifted by the levers and the shift accumulates over time.
 */
export function applyWhatIf(forecastSeries, granularity, levers) {
  const { incomePct = 0, expensePct = 0, monthly = 0, oneOff = 0, oneOffDate = "" } = levers || {};
  if (!forecastSeries || !forecastSeries.length) return null;
  if (!incomePct && !expensePct && !monthly && !oneOff) return null;

  const isMonth = granularity === "month";
  const oneOffKey = isMonth ? oneOffDate.slice(0, 7) : oneOffDate;
  let oneOffDone = false;
  let shift = 0;

  return forecastSeries.map((p) => {
    shift += (Number(p.expected_in) || 0) * (incomePct / 100);
    shift -= (Number(p.expected_out) || 0) * (expensePct / 100);
    // Recurring change lands once per month: every point in monthly mode, on the 1st in daily mode.
    if (monthly && (isMonth || String(p.date).endsWith("-01"))) shift += monthly;
    if (oneOff && !oneOffDone && (!oneOffKey || p.date >= oneOffKey)) {
      shift += oneOff;
      oneOffDone = true;
    }
    return Math.round((Number(p.balance) + shift) * 100) / 100;
  });
}

function lowestPoint(dates, values) {
  let idx = 0;
  values.forEach((v, i) => {
    if (v < values[idx]) idx = i;
  });
  return { date: dates[idx], value: values[idx] };
}

export function createWhatIfController(els, { onChange }) {
  const { incomeInput, incomeOut, expenseInput, expenseOut, monthlyInput, oneOffInput, oneOffDateInput } =
    els;

  function levers() {
    return {
      incomePct: Number(incomeInput.value) || 0,
      expensePct: Number(expenseInput.value) || 0,
      monthly: Number(monthlyInput.value) || 0,
      oneOff: Number(oneOffInput.value) || 0,
      oneOffDate: oneOffDateInput.value || "",
    };
  }

  function compute(state) {
    const la = (state && state.liquidity_agent) || {};
    return applyWhatIf(la.forecast_series, la.granularity, levers());
  }

  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let resizeAnimation;
  function resizePanel(update) {
    resizeAnimation = animateHeight(els.panel, update);
  }
  reducedMotion.addEventListener('change', () => {
    if (reducedMotion.matches) resizeAnimation?.cancel();
  });

  function renderSummary(state) {
    resizePanel(() => renderSummaryContent(state));
  }

  function renderSummaryContent(state) {
    const la = (state && state.liquidity_agent) || {};
    const fore = la.forecast_series || [];
    const balances = compute(state);
    els.panel.classList.toggle("active", !!balances);
    if (!balances) {
      els.summary.textContent = "";
      els.summary.classList.add("hidden");
      return;
    }
    els.summary.classList.remove("hidden");
    const cur = state.currency || "EUR";
    const dates = fore.map((p) => p.date);
    const low = lowestPoint(dates, balances);
    const end = balances[balances.length - 1];
    const baseEnd = Number(fore[fore.length - 1].balance);
    const diff = end - baseEnd;
    const firstNeg = balances.findIndex((v) => v < 0);
    const negText =
      firstNeg >= 0
        ? `<strong class="neg">Goes below ${escapeHtml(fmtZero(cur))} on ${escapeHtml(dates[firstNeg])}.</strong> `
        : `<strong class="pos">Stays above ${escapeHtml(fmtZero(cur))}.</strong> `;
    els.summary.innerHTML =
      negText +
      `Lowest: <span class="num">${fmtMoney(low.value, cur)}</span> (${escapeHtml(low.date)}) · ` +
      `End of forecast: <span class="num">${fmtMoney(end, cur)}</span> ` +
      `(<span class="num ${diff >= 0 ? "pos" : "neg"}">${diff >= 0 ? "+" : ""}${fmtMoney(diff, cur)}</span> vs forecast)`;
  }

  function syncOutputs() {
    const pct = (v) => `${Number(v) > 0 ? "+" : ""}${v}%`;
    incomeOut.textContent = pct(incomeInput.value);
    expenseOut.textContent = pct(expenseInput.value);
    els.panel.querySelectorAll('[data-whatif-preset]').forEach(button => {
      const [lever, value] = button.dataset.whatifPreset.split(':');
      button.setAttribute('aria-pressed', String(Number(els[PRESET_LEVERS[lever]].value) === Number(value)));
    });
  }

  function changed() {
    syncOutputs();
    onChange();
  }

  function reset() {
    incomeInput.value = "0";
    expenseInput.value = "0";
    monthlyInput.value = "0";
    oneOffInput.value = "0";
    oneOffDateInput.value = "";
    syncOutputs();
  }

  [incomeInput, expenseInput, monthlyInput, oneOffInput, oneOffDateInput].forEach((el) =>
    el.addEventListener("input", changed)
  );
  els.resetBtn.addEventListener("click", () => {
    reset();
    onChange();
  });
  const tuneToggle = els.panel.querySelector('#whatif-tune-toggle');
  tuneToggle.addEventListener('click', () => {
    const expanded = tuneToggle.getAttribute('aria-expanded') !== 'true';
    resizePanel(() => {
      tuneToggle.setAttribute('aria-expanded', String(expanded));
      els.panel.querySelector('#whatif-tuning').classList.toggle('hidden', !expanded);
    });
  });
  els.panel.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-whatif-preset]");
    if (!btn) return;
    const [lever, value] = btn.getAttribute("data-whatif-preset").split(":");
    const input = els[PRESET_LEVERS[lever]];
    if (input) input.value = Number(input.value) === Number(value) ? '0' : value;
    changed();
  });
  syncOutputs();

  return { compute, renderSummary, reset };
}
