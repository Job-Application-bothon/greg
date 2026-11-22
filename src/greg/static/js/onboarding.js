import { fmtMoney } from "./format.js";
import { animateHeight } from "./controls.js";

// Delegate native disclosures, including optional CSV fields and dynamic details.
document.addEventListener('click', event => {
  const summary = event.target.closest('summary');
  const details = summary?.parentElement;
  if (!details?.matches('details') || event.defaultPrevented) return;
  if (event.target.closest('a, button, input, select, textarea')) return;
  event.preventDefault();
  animateHeight(details, () => { details.open = !details.open; });
});

const DEFAULT_BANK_ACTION_HTML = 'Retrieve bank data <span aria-hidden="true">↗</span>';

const main = document.querySelector("main");
main.insertAdjacentHTML("afterbegin", `    <section id="source-chooser" class="entry-stage" aria-label="Choose your financial data source">
      <div class="source-options">
        <button type="button" id="choose-csv" class="source-card"><span class="source-icon" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M8 4h11l6 6v18H8zM19 4v7h6M12 17h9M12 22h9"/></svg></span><span class="source-title">Have a CSV file?</span><span class="source-copy">Drop your statement here, or browse files.</span><span class="source-action">Choose a file <span aria-hidden="true">↗</span></span></button>
        <span class="source-or">OR</span>
        <button type="button" id="choose-bank" class="source-card"><span class="source-icon" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="m4 11 12-7 12 7zM7 15v10M16 15v10M25 15v10M4 28h24"/></svg></span><span class="source-title">Connect your bank</span><span class="source-copy">Retrieve a simulated bank statement automatically.</span><span class="source-action" id="choose-bank-action">${DEFAULT_BANK_ACTION_HTML}</span></button>
      </div>
      <p id="source-error" class="source-error hidden" role="alert"></p>
      <button type="button" id="cancel-source" class="secondary hidden">Back to my overview</button>
    </section>
    <section id="balance-overview" class="balance-stage hidden" aria-labelledby="balance-title">
      <div><p class="entry-eyebrow" id="overview-source">YOUR MONTH AT A GLANCE</p><h1 id="balance-title">Month-end balance</h1><p id="overview-balance" class="balance-number num"></p><p id="overview-month" class="overview-month"></p></div>
      <div class="overview-metrics"><div><span>Income</span><strong id="overview-income" class="num"></strong></div><div><span>Expenses</span><strong id="overview-expenses" class="num"></strong></div><div><span>Net change</span><strong id="overview-net" class="num"></strong></div></div>
      <a href="#dashboard" class="scroll-cue">A closer look below <span aria-hidden="true">↓</span></a>
    </section>
`);

const $ = (id) => document.getElementById(id);
const chooser = $("source-chooser");
const chooseCsvBtn = $("choose-csv");
const chooseBankBtn = $("choose-bank");
const chooseBankAction = $("choose-bank-action");
const sourceError = $("source-error");
const overview = $("balance-overview");
const tools = document.createElement("details");
tools.id = "data-tools";
tools.innerHTML = '<summary class="hidden">CSV import</summary>';
tools.className = "hidden";
const inputGrid = main.querySelector(".grid-2");
const dashboard = document.createElement("section");
dashboard.id = "dashboard";
dashboard.setAttribute("aria-label", "Financial dashboard");
main.append(dashboard);
dashboard.append(tools);
tools.append(inputGrid);
dashboard.append($("empty-view"), $("workspace-view"));
dashboard.append(tools);

const graphActions = document.createElement("div");
graphActions.id = "graph-actions";
graphActions.className = "hidden";
graphActions.innerHTML =
  '<details id="manual-entry"><summary><span aria-hidden="true">＋</span> Add manually <span class="manual-chevron" aria-hidden="true">⌄</span></summary><div class="manual-entry-panel"><h2>Add a transaction</h2><p class="subtitle">Record an income or expense that isn’t in your statement.</p></div></details><div class="bank-demo-action"><button type="button" class="secondary" id="connect-bank-demo" aria-expanded="false" aria-controls="bank-demo-hint">Connect to bank <span class="demo-tag">Demo</span></button><p id="bank-demo-hint" class="subtitle hidden" role="status">Bank connection preview only. No bank is connected.</p></div>';
const manualSection = $("manual-form").closest("section");
graphActions.querySelector(".manual-entry-panel").append($("manual-form"));
manualSection.remove();
graphActions.querySelector("#add-op-btn").textContent = "Add transaction";
dashboard.append(graphActions);
graphActions.querySelector("#connect-bank-demo").addEventListener("click", (event) => {
  const button = event.currentTarget;
  const expanded = button.getAttribute("aria-expanded") !== "true";
  animateHeight(button.parentElement, () => {
    button.setAttribute("aria-expanded", String(expanded));
    show($("bank-demo-hint"), expanded);
  });
});

const assistantStage = document.createElement("section");
assistantStage.id = "assistant-stage";
assistantStage.className = "assistant-stage hidden";
assistantStage.setAttribute("aria-labelledby", "assistant-stage-title");
assistantStage.innerHTML =
  '<h2 id="assistant-stage-title">Questions about<br>your numbers?</h2><p class="assistant-stage-copy">If something isn’t clear, just ask Greg.<br>Your finance assistant can help you understand the details.</p>';
main.append(assistantStage);
const assistantButton = $("chat-launcher");
assistantButton.innerHTML =
  '<span>Chat with Greg</span><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';
assistantStage.append(assistantButton);

const floatingChatButton = document.createElement("button");
floatingChatButton.id = "floating-chat-launcher";
floatingChatButton.type = "button";
floatingChatButton.setAttribute("aria-label", "Open chat with Greg");
floatingChatButton.setAttribute("aria-controls", "greg-messenger");
floatingChatButton.setAttribute("aria-expanded", "false");
floatingChatButton.innerHTML =
  '<svg width="25" height="25" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M20 11.5a8 8 0 0 1-8 8H4l-2 2v-10a9 9 0 0 1 18 0Z"/><path d="M7 9h8M7 13h5"/></svg>';
document.body.append(floatingChatButton);
floatingChatButton.addEventListener("click", () => assistantButton.click());
document.body.append($("greg-messenger"));

const change = document.createElement("button");
change.id = "change-source";
change.type = "button";
change.className = "secondary hidden";
change.textContent = "Change data source";
$("reset-btn").before(change);

let savedState = null;
let bankConnectHandler = null;
let isConnectingBank = false;

const bankRibbon = document.createElement("div");
bankRibbon.className = "bank-ribbon";
bankRibbon.setAttribute("aria-hidden", "true");
chooseBankBtn.prepend(bankRibbon);
const bankRibbonResize = new ResizeObserver(() => {
  const title = chooseBankBtn.querySelector(".source-title");
  if (title) bankRibbon.style.height = `${Math.max(0, title.offsetTop - 18)}px`;
});
bankRibbonResize.observe(chooseBankBtn);

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
reducedMotion.addEventListener("change", () => {
  if (reducedMotion.matches) {
    for (const el of [chooser, overview, dashboard]) {
      for (const animation of el.getAnimations()) animation.cancel();
    }
  }
});

function show(el, visible) {
  el.classList.toggle("hidden", !visible);
  if (el === overview) assistantStage.classList.toggle("hidden", !visible);
  if (visible && [overview, dashboard].includes(el)) {
    document.documentElement.classList.remove("source-screen");
  }
}

const headerResize = new ResizeObserver(() => {
  document.documentElement.style.setProperty(
    "--stage-header",
    `${document.querySelector(".site-head").getBoundingClientRect().height}px`
  );
});
headerResize.observe(document.querySelector(".site-head"));

function graphScrollBoundary() {
  return (
    dashboard.getBoundingClientRect().top +
    window.scrollY -
    document.querySelector(".site-head").getBoundingClientRect().height
  );
}

function updateScrollMode(delta) {
  const root = document.documentElement;
  if (!root.classList.contains("staged-scroll") || !delta) return;
  root.classList.toggle(
    "free-scroll",
    window.scrollY > graphScrollBoundary() + 2 || (delta > 0 && window.scrollY >= graphScrollBoundary() - 2)
  );
}

window.addEventListener(
  "wheel",
  (event) => {
    if (event.ctrlKey || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    const wasFree = document.documentElement.classList.contains("free-scroll");
    updateScrollMode(event.deltaY);
    if (!wasFree && document.documentElement.classList.contains("free-scroll")) {
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1;
      window.scrollBy({ top: event.deltaY * unit, behavior: "instant" });
    } else if (wasFree && !document.documentElement.classList.contains("free-scroll")) {
      event.preventDefault();
      window.scrollTo({
        top:
          overview.getBoundingClientRect().top +
          window.scrollY -
          document.querySelector(".site-head").getBoundingClientRect().height,
        behavior: reducedMotion.matches ? "instant" : "smooth",
      });
    }
  },
  { passive: false }
);

let touchY = null;
window.addEventListener(
  "touchstart",
  (event) => {
    touchY = event.touches[0]?.clientY ?? null;
  },
  { passive: true }
);
window.addEventListener(
  "touchmove",
  (event) => {
    if (touchY !== null && event.touches[0]) updateScrollMode(touchY - event.touches[0].clientY);
  },
  { passive: true }
);
window.addEventListener(
  "scroll",
  () => {
    if (document.documentElement.classList.contains("staged-scroll") && window.scrollY < graphScrollBoundary() - 2) {
      document.documentElement.classList.remove("free-scroll");
    }
  },
  { passive: true }
);

function top() {
  document.documentElement.classList.remove("free-scroll");
  window.scrollTo({ top: 0, behavior: "instant" });
}

function enter(el) {
  if (!reducedMotion.matches) {
    el.animate(
      [
        { opacity: 0, transform: "translateY(14px)" },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { duration: 550, easing: "cubic-bezier(.2,.7,.2,1)" }
    );
  }
}

function setSourceError(message = "") {
  if (!sourceError) return;
  sourceError.textContent = message;
  show(sourceError, Boolean(message));
}

function setBankConnectingState(active, statusText = "Connecting to bank…") {
  isConnectingBank = active;
  chooseBankBtn.disabled = active;
  chooseCsvBtn.disabled = active;
  chooseBankBtn.classList.toggle("is-connecting", active);
  chooseBankBtn.setAttribute("aria-busy", String(active));
  if (active) {
    chooseBankAction.innerHTML = `${statusText} <span aria-hidden="true"><span class="bank-sync-spinner"></span></span>`;
  } else {
    chooseBankAction.innerHTML = DEFAULT_BANK_ACTION_HTML;
  }
}

function choose() {
  document.documentElement.classList.remove("staged-scroll");
  document.documentElement.classList.add("source-screen");
  setSourceError("");
  show(chooser, true);
  show(overview, false);
  show(dashboard, false);
  show($("cancel-source"), Boolean(savedState?.has_data));
  top();
  enter(chooser);
}

function summary(data) {
  tools.classList.remove("csv-review");
  show(tools, false);
  show(graphActions, true);
  show(graphActions.querySelector(".bank-demo-action"), true);
  $("connect-bank-demo").setAttribute("aria-expanded", "false");
  show($("bank-demo-hint"), false);
  document.documentElement.classList.add("staged-scroll");
  show(chooser, false);
  show(overview, true);
  show(dashboard, true);
  show(change, true);
  show($("empty-view"), false);
  const ms = data.month_summary;
  const isConnectedBank = data.data_source === "connected_bank";
  $("overview-source").textContent = isConnectedBank
    ? `CONNECTED BANK · ${(data.bank_account_name || "Account").toUpperCase()}`
    : "YOUR MONTH AT A GLANCE";
  $("balance-title").textContent = "Month-end balance";
  $("overview-balance").textContent = fmtMoney(ms.end_balance, data.currency);
  $("overview-month").textContent = data.selected_month_label;
  $("overview-income").textContent = fmtMoney(ms.total_income, data.currency);
  $("overview-expenses").textContent = fmtMoney(ms.total_expenses, data.currency);
  $("overview-net").textContent = fmtMoney(ms.profit ?? ms.net_change, data.currency);
  tools.open = false;
  enter(overview);
}

export function bindBankConnect(handler) {
  bankConnectHandler = handler;
}

export function syncOnboarding(state) {
  const hadData = Boolean(savedState?.has_data);
  const wasChoosing = !chooser.classList.contains("hidden");
  savedState = state;
  show($("reset-btn"), Boolean(state?.has_data));
  if (state?.has_data) {
    summary(state);
    if (!hadData || wasChoosing) top();
  } else {
    show(change, false);
    choose();
  }
}

change.addEventListener("click", choose);
$("cancel-source").addEventListener("click", () => {
  if (savedState?.has_data) {
    summary(savedState);
    top();
  }
});

chooseBankBtn.addEventListener("click", async () => {
  if (isConnectingBank || typeof bankConnectHandler !== "function") return;
  setSourceError("");
  setBankConnectingState(true, "Connecting to bank…");

  const stageTimer = setTimeout(() => {
    if (isConnectingBank) {
      setBankConnectingState(true, "Retrieving bank statement…");
    }
  }, 280);

  const minDelayMs = reducedMotion.matches ? 0 : 650;
  try {
    await Promise.all([
      bankConnectHandler(),
      new Promise((resolve) => setTimeout(resolve, minDelayMs)),
    ]);
  } catch (err) {
    setSourceError(err?.message || "Could not retrieve mock bank statement.");
  } finally {
    clearTimeout(stageTimer);
    setBankConnectingState(false);
  }
});

function reviewFile() {
  document.documentElement.classList.remove("staged-scroll");
  if (!$("csv-file").files.length) return;
  show(chooser, false);
  show(overview, false);
  show(dashboard, true);
  show($("empty-view"), false);
  show(change, true);
  tools.open = true;
  show(tools, true);
  show(graphActions, false);
  tools.classList.add("csv-review");
  tools.scrollIntoView({ block: "start", behavior: reducedMotion.matches ? "instant" : "smooth" });
}

chooseCsvBtn.addEventListener("click", () => $("csv-file").click());
$("csv-file").addEventListener("change", reviewFile);
for (const name of ["dragenter", "dragover"]) {
  chooseCsvBtn.addEventListener(name, (event) => {
    event.preventDefault();
    chooseCsvBtn.classList.add("drag-active");
  });
}
chooseCsvBtn.addEventListener("dragleave", () => chooseCsvBtn.classList.remove("drag-active"));
chooseCsvBtn.addEventListener("drop", (event) => {
  event.preventDefault();
  chooseCsvBtn.classList.remove("drag-active");
  const file = event.dataTransfer.files[0];
  if (!file || !file.name.toLowerCase().endsWith(".csv")) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  $("csv-file").files = transfer.files;
  $("csv-file").dispatchEvent(new Event("change", { bubbles: true }));
});

const observer = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        enter(entry.target);
        observer.unobserve(entry.target);
      }
    }
  },
  { threshold: 0.08 }
);
observer.observe(dashboard);
observer.observe(assistantStage);
show(dashboard, false);
