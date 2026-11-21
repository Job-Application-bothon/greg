/** CSV header detection, auto-guessing, currency inference, and manual column mapping UI. */
import { escapeHtml } from "./format.js";

const CURRENCY_HINT_RX =
  /(?:^|[_\s(\[-])(eur|pln|usd|chf|gbp|czk|sek|nok|dkk|cad|aud|jpy|huf|ron)(?:$|[_\s)\]-])/i;

export function parseCsvHeaderLine(line, delimiter) {
  const cells = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (ch === delimiter && !inQuotes) {
      cells.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  cells.push(cur);
  return cells.map((h) => h.trim()).filter((h) => h.length > 0);
}

export function detectCsvHeaders(text) {
  const cleaned = (text || "").replace(/^\uFEFF/, "").trim();
  if (!cleaned) return { delimiter: ",", headers: [] };
  const firstLine = cleaned.split(/\r?\n/, 1)[0] || "";
  const seps = [";", ",", "\t"];
  let bestSep = ",";
  let bestCount = 0;
  seps.forEach((sep) => {
    const count = firstLine.split(sep).length - 1;
    if (count > bestCount) {
      bestCount = count;
      bestSep = sep;
    }
  });
  return { delimiter: bestSep, headers: parseCsvHeaderLine(firstLine, bestSep) };
}

export function findBestHeader(headers, candidates) {
  const lowerMap = new Map(headers.map((h) => [h.toLowerCase().trim(), h]));
  for (const c of candidates) {
    if (lowerMap.has(c)) return lowerMap.get(c);
  }
  for (const h of headers) {
    const hl = h.toLowerCase().trim();
    if (candidates.some((c) => hl.includes(c))) return h;
  }
  return "";
}

export function inferCurrencyFromHeaderNames(names) {
  for (const name of names) {
    if (!name) continue;
    const match = CURRENCY_HINT_RX.exec(name);
    if (match) return match[1].toUpperCase();
  }
  return "";
}

function populateHeaderSelect(selectEl, headers, selectedVal, allowNone = false) {
  if (!selectEl) return;
  const opts = allowNone ? ['<option value="">— None —</option>'] : [];
  headers.forEach((h) => {
    opts.push(`<option value="${escapeHtml(h)}">${escapeHtml(h)}</option>`);
  });
  selectEl.innerHTML = opts.join("");
  if (selectedVal && headers.includes(selectedVal)) {
    selectEl.value = selectedVal;
  } else if (allowNone) {
    selectEl.value = "";
  } else if (headers.length > 0) {
    selectEl.value = headers[0];
  }
}

export function createCsvMappingController(els) {
  const {
    csvFileInput,
    csvMappingBox,
    mappingMeta,
    mapDateSelect,
    mapAmountModeSelect,
    mapAmountWrap,
    mapAmountSelect,
    mapIncomeWrap,
    mapIncomeSelect,
    mapExpensesWrap,
    mapExpensesSelect,
    mapCurrencySelect,
    mapDefaultCurrencyInput,
    mapCounterpartySelect,
    mapTypeSelect,
    mapDescriptionSelect,
  } = els;

  function updateAmountModeVisibility() {
    const isSplit = mapAmountModeSelect && mapAmountModeSelect.value === "split";
    if (mapAmountWrap) mapAmountWrap.classList.toggle("hidden", isSplit);
    if (mapIncomeWrap) mapIncomeWrap.classList.toggle("hidden", !isSplit);
    if (mapExpensesWrap) mapExpensesWrap.classList.toggle("hidden", !isSplit);
  }

  function syncDefaultCurrencyPlaceholder() {
    if (!mapDefaultCurrencyInput) return;
    const isSplit = mapAmountModeSelect && mapAmountModeSelect.value === "split";
    const cols = isSplit
      ? [mapIncomeSelect && mapIncomeSelect.value, mapExpensesSelect && mapExpensesSelect.value]
      : [mapAmountSelect && mapAmountSelect.value];
    const inferred = inferCurrencyFromHeaderNames(cols);
    mapDefaultCurrencyInput.value = inferred || "";
  }

  function populateFromText(text) {
    if (!csvMappingBox) return;
    const { delimiter, headers } = detectCsvHeaders(text);
    if (!headers.length) {
      csvMappingBox.classList.add("hidden");
      return;
    }
    const sepLabel = delimiter === "\t" ? "TAB" : `'${delimiter}'`;
    if (mappingMeta) {
      mappingMeta.textContent = `${headers.length} columns (${sepLabel})`;
    }

    const guessDate =
      findBestHeader(headers, [
        "booking date",
        "date",
        "value date",
        "transaction date",
        "buchungstag",
        "datum",
        "month",
        "week_ending",
        "week ending",
      ]) || headers[0];
    const guessAmount = findBestHeader(headers, ["amount", "betrag", "value", "sum"]);
    const guessIncome = findBestHeader(headers, [
      "income",
      "revenue",
      "inflow",
      "sales",
      "credit",
      "receipts",
      "money in",
    ]);
    const guessExpenses = findBestHeader(headers, [
      "expenses",
      "expense",
      "costs",
      "cost",
      "outflow",
      "spend",
      "debit",
      "money out",
    ]);
    const guessMode = !guessAmount && (guessIncome || guessExpenses) ? "split" : "single";

    populateHeaderSelect(mapDateSelect, headers, guessDate, false);
    if (mapAmountModeSelect) mapAmountModeSelect.value = guessMode;
    populateHeaderSelect(
      mapAmountSelect,
      headers,
      guessAmount || headers[Math.min(1, headers.length - 1)],
      false
    );
    populateHeaderSelect(mapIncomeSelect, headers, guessIncome, true);
    populateHeaderSelect(mapExpensesSelect, headers, guessExpenses, true);
    populateHeaderSelect(
      mapCurrencySelect,
      headers,
      findBestHeader(headers, ["currency", "währung", "curr"]),
      true
    );
    populateHeaderSelect(
      mapCounterpartySelect,
      headers,
      findBestHeader(headers, [
        "beneficiary/payer",
        "counterparty",
        "beneficiary",
        "payer",
        "payee",
        "name",
      ]),
      true
    );
    populateHeaderSelect(
      mapTypeSelect,
      headers,
      findBestHeader(headers, ["transaction type", "type", "category", "art"]),
      true
    );
    populateHeaderSelect(
      mapDescriptionSelect,
      headers,
      findBestHeader(headers, ["payment reference", "reference", "description", "details", "info"]),
      true
    );

    updateAmountModeVisibility();
    syncDefaultCurrencyPlaceholder();
    csvMappingBox.classList.remove("hidden");
  }

  function buildColumnMap() {
    if (!csvMappingBox || csvMappingBox.classList.contains("hidden")) return null;
    const mode = (mapAmountModeSelect && mapAmountModeSelect.value) || "single";
    const map = {
      date: mapDateSelect ? mapDateSelect.value : "",
      amount_mode: mode,
      currency: mapCurrencySelect ? mapCurrencySelect.value : "",
      default_currency: mapDefaultCurrencyInput
        ? mapDefaultCurrencyInput.value.trim().toUpperCase()
        : "",
      counterparty: mapCounterpartySelect ? mapCounterpartySelect.value : "",
      type: mapTypeSelect ? mapTypeSelect.value : "",
      description: mapDescriptionSelect ? mapDescriptionSelect.value : "",
    };
    if (mode === "split") {
      map.income = mapIncomeSelect ? mapIncomeSelect.value : "";
      map.expenses = mapExpensesSelect ? mapExpensesSelect.value : "";
    } else {
      map.amount = mapAmountSelect ? mapAmountSelect.value : "";
    }
    return map;
  }

  function reset() {
    if (csvFileInput) csvFileInput.value = "";
    if (csvMappingBox) csvMappingBox.classList.add("hidden");
  }

  if (csvFileInput) {
    csvFileInput.addEventListener("change", async () => {
      const file = csvFileInput.files[0];
      if (!file) {
        if (csvMappingBox) csvMappingBox.classList.add("hidden");
        return;
      }
      const headText = await file.slice(0, 8192).text();
      populateFromText(headText);
    });
  }

  if (mapAmountModeSelect) {
    mapAmountModeSelect.addEventListener("change", () => {
      updateAmountModeVisibility();
      syncDefaultCurrencyPlaceholder();
    });
  }

  [mapAmountSelect, mapIncomeSelect, mapExpensesSelect].forEach((el) => {
    if (el) el.addEventListener("change", syncDefaultCurrencyPlaceholder);
  });

  return { populateFromText, buildColumnMap, reset };
}
