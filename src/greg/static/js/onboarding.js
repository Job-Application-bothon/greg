import { fmtMoney } from "./format.js";

const main = document.querySelector("main");
main.insertAdjacentHTML("afterbegin", `    <section id="source-chooser" class="entry-stage" aria-labelledby="entry-title">
      <div class="entry-heading"><p class="entry-eyebrow">YOUR NUMBERS. A CLEARER PICTURE.</p><h1 id="entry-title">A little clarity.<br>A lot of possibility.</h1><p>Bring your finances together. Start with what you have.</p></div>
      <div class="source-options">
        <button type="button" id="choose-csv" class="source-card"><span class="source-icon" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M8 4h11l6 6v18H8zM19 4v7h6M12 17h9M12 22h9"/></svg></span><span class="source-title">Have a CSV file?</span><span class="source-copy">Drop your statement here, or browse files.<br>Review your columns before importing.</span><span class="source-action">Choose a file <span aria-hidden="true">↗</span></span></button>
        <span class="source-or">OR</span>
        <button type="button" id="choose-bank" class="source-card"><span class="source-icon" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="m4 11 12-7 12 7zM7 15v10M16 15v10M25 15v10M4 28h24"/></svg></span><span class="source-title">Connect your bank</span><span class="source-copy">Try the experience with your monthly totals.<br>No bank login. No connection. Just a preview.</span><span class="source-action">Explore the demo <span aria-hidden="true">↗</span></span></button>
      </div>
      <p class="entry-footnote">Your CSV stays in your existing Greg session. Bank demo inputs stay in this page.</p>
      <button type="button" id="cancel-source" class="secondary hidden">Back to my overview</button>
    </section>
    <section id="bank-entry" class="entry-stage hidden" aria-labelledby="bank-title">
      <div class="entry-heading"><p class="entry-eyebrow">MANUAL DEMO · NOT A BANK CONNECTION</p><h1 id="bank-title">Make it your own.</h1><p>A quick look at your month, using only the numbers you enter.<br>Nothing is imported or saved. Your dashboard data stays unchanged.</p></div>
      <form id="bank-demo-form" class="demo-form card">
        <div class="field"><label for="demo-account">Account name</label><input id="demo-account" maxlength="80" placeholder="e.g. Business account" required></div>
        <div class="field"><label for="demo-currency">Currency</label><select id="demo-currency"><option>EUR</option><option>USD</option><option>GBP</option><option>PLN</option></select></div>
        <div class="field"><label for="demo-balance">Current balance</label><input id="demo-balance" type="number" step="0.01" required placeholder="0.00"></div>
        <div class="field"><label for="demo-income">Monthly income</label><input id="demo-income" type="number" step="0.01" min="0" required placeholder="0.00"></div>
        <div class="field"><label for="demo-expenses">Monthly expenses</label><input id="demo-expenses" type="number" step="0.01" min="0" required placeholder="0.00"></div>
        <div class="demo-actions"><button type="button" id="bank-back" class="secondary">Back</button><button type="submit">Preview my overview <span aria-hidden="true">&nbsp;↗</span></button></div>
      </form>
    </section>
    <section id="balance-overview" class="balance-stage hidden" aria-labelledby="balance-title">
      <div><p class="entry-eyebrow" id="overview-source">YOUR MONTH AT A GLANCE</p><h1 id="balance-title">Current balance</h1><p id="overview-balance" class="balance-number num"></p><p id="overview-month" class="overview-month"></p></div>
      <div class="overview-metrics"><div><span>Income</span><strong id="overview-income" class="num"></strong></div><div><span>Expenses</span><strong id="overview-expenses" class="num"></strong></div><div><span>Net change</span><strong id="overview-net" class="num"></strong></div></div>
      <a href="#dashboard" class="scroll-cue">A closer look below <span aria-hidden="true">↓</span></a>
    </section>
`);

const $ = (id) => document.getElementById(id);
const chooser = $("source-chooser");
const bank = $("bank-entry");
const overview = $("balance-overview");
const tools = document.createElement("details");
tools.id = "data-tools";
tools.innerHTML = '<summary>Manage data &amp; add operations</summary>';
const inputGrid = main.querySelector(".grid-2");
const dashboard = document.createElement("section");
dashboard.id = "dashboard";
dashboard.setAttribute("aria-label", "Financial dashboard");
main.append(dashboard);
dashboard.append(tools);
tools.append(inputGrid);
dashboard.append($("empty-view"), $("workspace-view"));
const note = document.createElement("p");
note.className = "card hidden";
note.textContent = "Manual demo preview only. No bank is connected. No forecast is generated from these totals. Import a CSV to explore your real dashboard; any existing dashboard below still uses your saved data.";
dashboard.prepend(note);
const change = document.createElement("button");
change.id = "change-source";
change.type = "button";
change.className = "secondary hidden";
change.textContent = "Change data source";
$("reset-btn").before(change);
let savedState = null;
let preview = null;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
reducedMotion.addEventListener("change", () => {
  if (reducedMotion.matches) for (const el of [chooser, bank, overview, dashboard]) {
    for (const animation of el.getAnimations()) animation.cancel();
  }
});
function show(el, visible) { el.classList.toggle("hidden", !visible); }
function top() { window.scrollTo({ top: 0, behavior: "instant" }); }
function enter(el) {
  if (!reducedMotion.matches) el.animate([{opacity: 0, transform: "translateY(14px)"}, {opacity: 1, transform: "translateY(0)"}], {duration: 550, easing: "cubic-bezier(.2,.7,.2,1)"});
}
function choose() {
  show(chooser, true); show(bank, false); show(overview, false); show(dashboard, false);
  show($("cancel-source"), !!(preview || savedState?.has_data));
  top(); enter(chooser);
}
function summary(data, isPreview = false) {
  show(chooser, false); show(bank, false); show(overview, true); show(dashboard, true); show(change, true);
  show(note, isPreview); show($("empty-view"), false);
  const ms = data.month_summary;
  $("overview-source").textContent = isPreview ? `MANUAL DEMO · ${data.account}` : "YOUR MONTH AT A GLANCE";
  $("balance-title").textContent = isPreview ? "Current balance" : "Month-end balance";
  $("overview-balance").textContent = fmtMoney(ms.end_balance, data.currency);
  $("overview-month").textContent = data.selected_month_label;
  $("overview-income").textContent = fmtMoney(ms.total_income, data.currency);
  $("overview-expenses").textContent = fmtMoney(ms.total_expenses, data.currency);
  $("overview-net").textContent = fmtMoney(ms.profit ?? ms.net_change, data.currency);
  tools.open = false;
  enter(overview);
}
export function syncOnboarding(state) {
  const hadData = savedState?.has_data;
  savedState = state;
  preview = null;
  show($("reset-btn"), !!state?.has_data);
  if (state?.has_data) { summary(state); if (!hadData) top(); }
  else { show(change, false); choose(); }
}
change.addEventListener("click", choose);
$("cancel-source").addEventListener("click", () => summary(preview || savedState, !!preview));
$("choose-bank").addEventListener("click", () => {
  show(chooser, false); show(bank, true); top(); enter(bank); $("demo-account").focus({preventScroll: true});
});
$("bank-back").addEventListener("click", choose);
$("bank-demo-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const balance = Number($("demo-balance").value);
  const income = Number($("demo-income").value);
  const expenses = Number($("demo-expenses").value);
  if (![balance, income, expenses].every(Number.isFinite) || income < 0 || expenses < 0) return;
  preview = { account: $("demo-account").value.trim(), currency: $("demo-currency").value,
    selected_month_label: new Date().toLocaleDateString("en", {month: "long", year: "numeric"}),
    month_summary: {end_balance: balance, total_income: income, total_expenses: expenses, net_change: income - expenses} };
  summary(preview, true); top();
});
function reviewFile() {
  if (!$("csv-file").files.length) return;
  show(chooser, false); show(bank, false); show(overview, false); show(dashboard, true);
  show(note, false); show($("empty-view"), false); show(change, true);
  tools.open = true;
  tools.scrollIntoView({block: "start", behavior: reducedMotion.matches ? "instant" : "smooth"});
}
$("choose-csv").addEventListener("click", () => $("csv-file").click());
$("csv-file").addEventListener("change", reviewFile);
for (const name of ["dragenter", "dragover"]) $("choose-csv").addEventListener(name, (event) => {
  event.preventDefault(); $("choose-csv").classList.add("drag-active");
});
$("choose-csv").addEventListener("dragleave", () => $("choose-csv").classList.remove("drag-active"));
$("choose-csv").addEventListener("drop", (event) => {
  event.preventDefault(); $("choose-csv").classList.remove("drag-active");
  const file = event.dataTransfer.files[0];
  if (!file || !file.name.toLowerCase().endsWith(".csv")) return;
  const transfer = new DataTransfer(); transfer.items.add(file);
  $("csv-file").files = transfer.files;
  $("csv-file").dispatchEvent(new Event("change", {bubbles: true}));
});
const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) if (entry.isIntersecting) {
    enter(entry.target); observer.unobserve(entry.target);
  }
}, {threshold: 0.08});
observer.observe(dashboard);
show(dashboard, false);

