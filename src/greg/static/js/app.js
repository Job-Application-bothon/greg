/** Main Greg application orchestrator connecting API, chart, CSV mapping, and chat modules. */
import { bindBankConnect, syncOnboarding } from "./onboarding.js";
import { fetchHealth, fetchViewData, postJson } from "./api.js";
import { createChartController } from "./chart.js";
import { createChatController } from "./chat.js";
import { initForecastDotHover, initNumberSteppers, initStepCarousel } from "./controls.js";
import { createCsvMappingController } from "./csv-mapping.js";
import { escapeHtml, fmtMoney, fmtZero } from "./format.js";

initStepCarousel();
initNumberSteppers();
initForecastDotHover();

const statusEl = document.getElementById("status");
const llmDot = document.getElementById("llm-dot");
const resetBtn = document.getElementById("reset-btn");
const emptyView = document.getElementById("empty-view");
const workspaceView = document.getElementById("workspace-view");

const importForm = document.getElementById("import-form");
const csvFileInput = document.getElementById("csv-file");
const openingBalanceInput = document.getElementById("opening-balance");
const importInfo = document.getElementById("import-info");
const csvMappingBox = document.getElementById("csv-mapping-box");

const manualForm = document.getElementById("manual-form");
const opDateInput = document.getElementById("op-date");
const opKindInput = document.getElementById("op-kind");
const opAmountInput = document.getElementById("op-amount");
const opCpInput = document.getElementById("op-cp");
const opCatInput = document.getElementById("op-cat");

const monthSelect = document.getElementById("month-select");
const forecastModeSubtitle = document.getElementById("forecast-mode-subtitle");
const forecastMethodBar = document.getElementById("forecast-method-bar");

const kpiIncome = document.getElementById("kpi-income");
const kpiExpenses = document.getElementById("kpi-expenses");
const kpiNet = document.getElementById("kpi-net");
const kpiTxCount = document.getElementById("kpi-tx-count");
const kpiEndBal = document.getElementById("kpi-end-bal");
const kpiLowBal = document.getElementById("kpi-low-bal");

const expenseListEl = document.getElementById("expense-breakdown-list");
const incomeListEl = document.getElementById("income-breakdown-list");
const txTableSummary = document.getElementById("tx-table-summary");
const txTableBody = document.getElementById("tx-table-body");

const agentBanner = document.getElementById("agent-banner");
const agentAnalysisText = document.getElementById("agent-analysis-text");
const agentRecText = document.getElementById("agent-rec-text");
const upcomingBillsBody = document.getElementById("upcoming-bills-body");
const recLowest = document.getElementById("rec-lowest");
const recBelowLabel = document.getElementById("rec-below-label");
const recDays = document.getElementById("rec-days");
const recCredit = document.getElementById("rec-credit");
const aiExplainBtn = document.getElementById("ai-explain-btn");

const creditForm = document.getElementById("credit-form");
const creditInput = document.getElementById("credit-input");
const creditStatus = document.getElementById("credit-status");

let currentState = null;

if (opDateInput) {
  opDateInput.value = new Date().toISOString().slice(0, 10);
}

const chat = createChatController(
  {
    chatLauncher: document.getElementById("chat-launcher"),
    messenger: document.getElementById("greg-messenger"),
    chatCloseBtn: document.getElementById("chat-close"),
    messagesEl: document.getElementById("messages"),
    chatForm: document.getElementById("chat-form"),
    promptEl: document.getElementById("prompt"),
    sendBtn: document.getElementById("send-btn"),
    aiExplainBtn,
    chatPeriodContext: document.getElementById("chat-period-context"),
    chatPeriodModeEl: document.getElementById("chat-period-mode"),
    chatPeriodDatesEl: document.getElementById("chat-period-dates"),
    chatPeriodIncEl: document.getElementById("chat-period-inc"),
    chatPeriodExpEl: document.getElementById("chat-period-exp"),
    chatPeriodNetEl: document.getElementById("chat-period-net"),
    chatPeriodDismissBtn: document.getElementById("chat-period-dismiss"),
  },
  { getHorizonPayload: () => chart.getHorizonPayload() }
);

const chart = createChartController(
  {
    forecastPanel: document.getElementById("forecast-panel"),
    forecastChart: document.getElementById("forecast-chart"),
    chartTooltip: document.getElementById("chart-tooltip"),
    chartGranularityBadge: document.getElementById("chart-granularity-badge"),
    chartRangeLabel: document.getElementById("chart-range-label"),
    chartLegend: document.getElementById("chart-legend"),
    historyPillGroup: document.getElementById("history-pill-group"),
    forecastPillGroup: document.getElementById("forecast-pill-group"),
    dateJumpBar: document.getElementById("date-jump-bar"),
    chartFocusDateInput: document.getElementById("chart-focus-date"),
    chartFocusClearBtn: document.getElementById("chart-focus-clear"),
    chartFocusCard: document.getElementById("chart-focus-card"),
    minimapTrack: document.getElementById("minimap-track"),
    minimapSvg: document.getElementById("minimap-svg"),
    minimapMaskLeft: document.getElementById("minimap-mask-left"),
    minimapMaskRight: document.getElementById("minimap-mask-right"),
    minimapWindow: document.getElementById("minimap-window"),
    minimapHandleLeft: document.getElementById("minimap-handle-left"),
    minimapHandleRight: document.getElementById("minimap-handle-right"),
    minimapHorizonSummary: document.getElementById("minimap-horizon-summary"),
    periodActionBarWrap: document.getElementById("period-action-bar-wrap"),
    periodRangeDatesEl: document.getElementById("period-range-dates"),
    btnAnalyzePeriod: document.getElementById("btn-analyze-period"),
    btnClearPeriod: document.getElementById("btn-clear-period"),
  },
  {
    getCurrentState: () => currentState,
    onHorizonChange: () => fetchView(),
    onAnalyzePeriod: (payload) => chat.receivePeriodAnalysis(payload),
  }
);

const csvMapping = createCsvMappingController({
  csvFileInput,
  csvMappingBox,
  mappingMeta: document.getElementById("mapping-meta"),
  mapDateSelect: document.getElementById("map-date"),
  mapAmountModeSelect: document.getElementById("map-amount-mode"),
  mapAmountWrap: document.getElementById("map-amount-wrap"),
  mapAmountSelect: document.getElementById("map-amount"),
  mapIncomeWrap: document.getElementById("map-income-wrap"),
  mapIncomeSelect: document.getElementById("map-income"),
  mapExpensesWrap: document.getElementById("map-expenses-wrap"),
  mapExpensesSelect: document.getElementById("map-expenses"),
  mapCurrencySelect: document.getElementById("map-currency"),
  mapDefaultCurrencyInput: document.getElementById("map-default-currency"),
  mapCounterpartySelect: document.getElementById("map-counterparty"),
  mapTypeSelect: document.getElementById("map-type"),
  mapDescriptionSelect: document.getElementById("map-description"),
});

async function loadStatus() {
  try {
    const data = await fetchHealth();
    llmDot.className = data.llm_configured ? "status-dot" : "status-dot standby";
    const llmText = data.llm_configured ? `Model: ${data.llm_model || "auto"}` : "LLM not configured";
    statusEl.textContent = `Pod: ${data.pod} · ${llmText}`;
  } catch (err) {
    llmDot.className = "status-dot standby";
    statusEl.textContent = `Health check failed: ${err.message}`;
  }
}

async function fetchView() {
  const data = await fetchViewData(chart.getHorizonPayload());
  renderState(data);
}

function renderBreakdown(container, categories, currency, isExpense) {
  if (!categories || categories.length === 0) {
    container.innerHTML = `<div class="subtitle">No ${
      isExpense ? "expenses" : "income"
    } recorded in this month.</div>`;
    return;
  }
  container.innerHTML = categories
    .map((cat) => {
      const peopleRows = cat.people
        .map(
          (p) => `
          <li class="person-item">
            <div>
              <div><strong>${escapeHtml(p.name)}</strong></div>
              <div class="person-meta">${p.count} payment${p.count > 1 ? "s" : ""}</div>
            </div>
            <div class="num ${isExpense ? "neg" : "pos"}" style="font-weight: 600">
              ${isExpense ? "-" : "+"}${fmtMoney(p.total, currency)}
            </div>
          </li>`
        )
        .join("");

      return `
        <div class="cat-block">
          <div class="cat-header">
            <span>${escapeHtml(cat.category)} <span class="badge">${cat.count}</span></span>
            <span class="num ${isExpense ? "neg" : "pos"}">${isExpense ? "-" : "+"}${fmtMoney(
              cat.total,
              currency
            )}</span>
          </div>
          <ul class="person-list">${peopleRows}</ul>
        </div>`;
    })
    .join("");
}

function formatMethodBadge(code) {
  const map = {
    classic: "Classic (Seller Method)",
    network: "Own Model (Neural Network)",
    ets: "StatsForecast · ETS",
    arima: "StatsForecast · ARIMA",
    theta: "StatsForecast · Theta",
    combo: "StatsForecast · Combo (ETS + ARIMA + Theta)",
  };
  return map[code] || code || "Classic";
}

function renderMethodBar(la) {
  if (!forecastMethodBar) return;
  if (la.engine === "greg_planner" && la.planner_meta) {
    const pm = la.planner_meta;
    const inCode = pm.inflow_method;
    const outCode = pm.outflow_method;
    const pillsHtml =
      inCode === outCode
        ? `<span class="method-pill">${escapeHtml(formatMethodBadge(inCode))}</span>`
        : `<span class="method-pill">Inflow: ${escapeHtml(formatMethodBadge(inCode))}</span>` +
          `<span class="method-pill">Outflow: ${escapeHtml(formatMethodBadge(outCode))}</span>`;
    const notes = pm.notes || [];
    const detailsHtml = notes.length
      ? `<details class="method-details"><summary>Why this method was chosen</summary><ul class="method-notes-list">${notes
          .map((n) => `<li>${escapeHtml(n)}</li>`)
          .join("")}</ul></details>`
      : "";
    forecastMethodBar.innerHTML = `
      <div class="method-bar-head">
        <strong>Method Chosen:</strong>
        ${pillsHtml}
        <span class="method-meta">Engine: greg_planner${pm.network_loaded ? " (neural net ready)" : ""}</span>
      </div>
      ${detailsHtml}
    `;
  } else {
    forecastMethodBar.innerHTML = `
      <div class="method-bar-head">
        <strong>Method Chosen:</strong>
        <span class="method-pill">Daily Fallback (Recurring Bills + Weekday Average)</span>
        <span class="method-meta">${escapeHtml(la.fallback_reason || "Under 12 full months of history")}</span>
      </div>
    `;
  }
  forecastMethodBar.classList.remove("hidden");
}

function renderState(state) {
  currentState = state;
  syncOnboarding(state);
  if (!state || !state.has_data) {
    emptyView.classList.remove("hidden");
    workspaceView.classList.add("hidden");
    resetBtn.classList.add("hidden");
    importInfo.textContent = "";
    return;
  }

  emptyView.classList.add("hidden");
  workspaceView.classList.remove("hidden");
  resetBtn.classList.remove("hidden");

  const cur = state.currency || "EUR";
  openingBalanceInput.value = state.opening_balance || 0;
  importInfo.textContent =
    state.data_source === "connected_bank"
      ? `Connected bank: ${state.bank_account_name || "Bank Account"} (${state.filename} · ${state.total_transactions} operations)`
      : state.filename
      ? `Loaded: ${state.filename} (${state.total_transactions} operations)`
      : `${state.total_transactions} manual operation(s) loaded`;

  monthSelect.innerHTML = state.months
    .map(
      (m) =>
        `<option value="${m.key}" ${m.key === state.selected_month ? "selected" : ""}>${escapeHtml(
          m.label
        )}</option>`
    )
    .join("");

  const ms = state.month_summary;
  const profitVal = ms.profit ?? ms.net_change;
  kpiIncome.textContent = `+${fmtMoney(ms.total_income, cur)}`;
  kpiExpenses.textContent = `-${fmtMoney(ms.total_expenses, cur)}`;
  kpiNet.textContent = (profitVal >= 0 ? "+" : "") + fmtMoney(profitVal, cur);
  kpiNet.className = `kpi-value num ${profitVal >= 0 ? "pos" : "neg"}`;
  kpiTxCount.textContent = `${ms.transaction_count} operations in ${state.selected_month_label}`;

  kpiEndBal.textContent = fmtMoney(ms.end_balance, cur);
  kpiEndBal.className = `kpi-value num ${ms.end_balance >= 0 ? "pos" : "neg"}`;
  kpiLowBal.textContent = `Lowest in month: ${fmtMoney(ms.lowest_balance, cur)} (on ${ms.lowest_balance_date})`;

  renderBreakdown(expenseListEl, state.expense_breakdown, cur, true);
  renderBreakdown(incomeListEl, state.income_breakdown, cur, false);

  txTableSummary.textContent = `Show all ${state.month_transactions.length} operations in ${state.selected_month_label}`;
  txTableBody.innerHTML = state.month_transactions
    .map(
      (t) => `
      <tr>
        <td>${escapeHtml(t.date)}</td>
        <td><strong>${escapeHtml(t.counterparty)}</strong></td>
        <td><span class="badge">${escapeHtml(t.category)}</span></td>
        <td>${escapeHtml(t.description)}</td>
        <td class="right num ${t.amount >= 0 ? "pos" : "neg"}" style="font-weight: 600">
          ${t.amount >= 0 ? "+" : ""}${fmtMoney(t.amount, cur)}
        </td>
        <td class="right">
          ${
            t.source === "manual"
              ? `<button type="button" class="danger" data-del-id="${escapeHtml(t.id)}">Delete</button>`
              : ""
          }
        </td>
      </tr>`
    )
    .join("");

  const la = state.liquidity_agent;
  chart.syncGranularity(la);
  if (forecastModeSubtitle) {
    if (la.granularity === "month" && la.planner_meta) {
      forecastModeSubtitle.textContent =
        `Monthly seasonal forecast · Auto (Inflow: ${la.planner_meta.inflow_method_label}, Outflow: ${la.planner_meta.outflow_method_label})`;
    } else {
      forecastModeSubtitle.textContent = "Daily short-term forecast · Under 12 months of history";
    }
  }

  renderMethodBar(la);

  if (la.goes_below_zero) {
    agentBanner.className = "alert-box warn";
    agentBanner.innerHTML = `<strong>Cash Shortfall Predicted:</strong> ${escapeHtml(
      la.recommendation_summary
    )}`;
  } else {
    agentBanner.className = "alert-box safe";
    agentBanner.innerHTML = `<strong>Account Stays Above ${escapeHtml(fmtZero(cur))}:</strong> ${escapeHtml(
      la.recommendation_summary
    )}`;
  }

  if (agentAnalysisText) agentAnalysisText.textContent = la.analysis_summary;
  if (agentRecText) agentRecText.textContent = la.recommendation_summary;
  if (recLowest) {
    recLowest.textContent = `${fmtMoney(la.lowest_predicted_balance, cur)} (on ${la.lowest_predicted_date})`;
    recLowest.className = la.lowest_predicted_balance < 0 ? "neg" : "pos";
  }
  if (recBelowLabel) {
    const zeroText = fmtZero(cur);
    recBelowLabel.textContent =
      la.granularity === "month" ? `Months Below ${zeroText}:` : `Days Below ${zeroText}:`;
  }
  if (recDays) {
    recDays.textContent =
      la.granularity === "month"
        ? `${la.months_below_zero || 0} month(s)`
        : `${la.days_below_zero || 0} day(s)`;
  }
  if (recCredit) recCredit.textContent = fmtMoney(la.recommended_credit_amount, cur);

  if (creditInput && creditStatus) {
    creditInput.value = la.recommended_credit_amount;
    creditStatus.innerHTML =
      '<span class="subtitle">Preview only — credit approval simulation does not modify the model or chart.</span>';
  }

  if (upcomingBillsBody) {
    if (la.upcoming_bills.length === 0) {
      const emptyMsg =
        la.engine === "greg_planner"
          ? "Monthly seasonal planner active — individual regular bill dates are shown in daily mode (&lt; 12 months)."
          : "No regular bills detected yet.";
      upcomingBillsBody.innerHTML = `<tr><td colspan="3" class="subtitle">${emptyMsg}</td></tr>`;
    } else {
      upcomingBillsBody.innerHTML = la.upcoming_bills
        .map(
          (b) => `
          <tr>
            <td>${escapeHtml(b.date)}</td>
            <td>${escapeHtml(b.counterparty)}</td>
            <td class="right neg num">${fmtMoney(b.amount, cur)}</td>
          </tr>`
        )
        .join("");
    }
  }

  chart.render(state);
}

bindBankConnect(async () => {
  chart.resetAll();
  chat.clearPeriodContext();
  if (chat.isOpen()) chat.setOpen(false);
  const res = await postJson("/api/bank/connect", {});
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || "Failed to retrieve bank statement");
  }
  renderState(data);
});

importForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = csvFileInput.files[0];
  if (!file) return;
  const text = await file.text();
  chart.resetAll();
  chat.clearPeriodContext();
  const payload = {
    csv_text: text,
    filename: file.name,
    opening_balance: Number(openingBalanceInput.value || 0),
  };
  const columnMap = csvMapping.buildColumnMap();
  if (columnMap) payload.column_map = columnMap;
  const res = await postJson("/api/import", payload);
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "Failed to import CSV");
    return;
  }
  renderState(data);
});

openingBalanceInput.addEventListener("change", async () => {
  if (!currentState || !currentState.has_data) return;
  const res = await postJson(
    "/api/opening-balance",
    Object.assign({ opening_balance: Number(openingBalanceInput.value || 0) }, chart.getHorizonPayload())
  );
  if (res.ok) renderState(await res.json());
});

manualForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const res = await postJson(
    "/api/operations",
    Object.assign(
      {
        date: opDateInput.value,
        kind: opKindInput.value,
        amount: Number(opAmountInput.value),
        counterparty: opCpInput.value,
        category: opCatInput.value,
      },
      chart.getHorizonPayload()
    )
  );
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "Could not add operation");
    return;
  }
  opAmountInput.value = "";
  opCpInput.value = "";
  opCatInput.value = "Other";
  renderState(data);
});

txTableBody.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-del-id]");
  if (!btn) return;
  const id = btn.getAttribute("data-del-id");
  const res = await postJson(
    "/api/operations/delete",
    Object.assign({ id }, chart.getHorizonPayload())
  );
  if (res.ok) renderState(await res.json());
});

// Step C ("Approve Credit Plan") stays a preview-only no-op per UX specification.
if (creditForm) {
  creditForm.addEventListener("submit", (e) => {
    e.preventDefault();
  });
}

resetBtn.addEventListener("click", async () => {
  const res = await postJson("/api/reset", {});
  if (res.ok) {
    csvMapping.reset();
    chart.resetAll();
    chat.clearPeriodContext();
    if (chat.isOpen()) chat.setOpen(false);
    renderState(await res.json());
  }
});

loadStatus();
fetchView();
