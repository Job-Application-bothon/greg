/** Commerzbank loan request (demo): shows the forecast balance with the loan paid out, no backend call. */
import { escapeHtml, fmtMoney, fmtZero } from "./format.js";

export const LOAN_BANK = "Commerzbank";

/** Return forecast balances with `amount` added from the first forecast point on, or null. */
export function applyLoan(forecastSeries, amount) {
  if (!forecastSeries || !forecastSeries.length || !(amount > 0)) return null;
  return forecastSeries.map((p) => Math.round((Number(p.balance) + amount) * 100) / 100);
}

export function createLoanController(els, { onChange }) {
  const { toggle, panel, amountInput, requestBtn, status } = els;
  let requested = 0;
  let prefilled = null;

  function amount() {
    return Math.max(0, Number(amountInput.value) || 0);
  }

  function compute(state) {
    const la = (state && state.liquidity_agent) || {};
    return applyLoan(la.forecast_series, requested);
  }

  function render(state) {
    const la = (state && state.liquidity_agent) || {};
    const recommended = Number(la.recommended_credit_amount) || 0;
    if (!requested && recommended !== prefilled) amountInput.value = String(recommended);
    prefilled = recommended;
    requestBtn.textContent = requested ? "Withdraw loan request" : `Request a loan from ${LOAN_BANK}`;
    requestBtn.className = requested ? "secondary" : "";
    requestBtn.setAttribute("aria-pressed", String(!!requested));
    requestBtn.disabled = !requested && amount() <= 0;

    const balances = compute(state);
    if (!balances) {
      status.innerHTML = `<span class="subtitle">Enter an amount and request the loan to see it on the forecast chart. Demo only.</span>`;
      return;
    }
    const cur = state.currency || "EUR";
    const fore = la.forecast_series || [];
    let low = 0;
    balances.forEach((v, i) => {
      if (v < balances[low]) low = i;
    });
    const firstNeg = balances.findIndex((v) => v < 0);
    const outcome =
      firstNeg >= 0
        ? `<strong class="neg">Still goes below ${escapeHtml(fmtZero(cur))} on ${escapeHtml(fore[firstNeg].date)}.</strong>`
        : `<strong class="pos">Stays above ${escapeHtml(fmtZero(cur))}.</strong>`;
    status.innerHTML =
      `Loan of <span class="num">${fmtMoney(requested, cur)}</span> requested from ${LOAN_BANK} - shown in green on the chart. ` +
      `${outcome} Lowest: <span class="num">${fmtMoney(balances[low], cur)}</span> (${escapeHtml(fore[low].date)}). ` +
      `<span class="subtitle">Demo only.</span>`;
  }

  function reset() {
    requested = 0;
    prefilled = null;
  }

  toggle.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    toggle.setAttribute("aria-expanded", String(!panel.hidden));
  });
  requestBtn.addEventListener("click", () => {
    requested = requested ? 0 : amount();
    onChange();
  });
  ["input", "change"].forEach((type) =>
    amountInput.addEventListener(type, () => {
      if (requested) requested = amount();
      onChange();
    })
  );

  return { compute, render, reset };
}
