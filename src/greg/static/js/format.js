/** Currency formatting and HTML escaping helpers. */

export function fmtMoney(val, currency = "EUR") {
  const num = Number(val || 0);
  const sym = currency === "EUR" ? "€" : `${currency} `;
  const sign = num < 0 ? "-" : "";
  return (
    sign +
    sym +
    Math.abs(num).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}

export function fmtZero(currency = "EUR") {
  return fmtMoney(0, currency).replace(/\.00$/, "");
}

export function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}
