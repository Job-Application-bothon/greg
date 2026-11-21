/** HTTP API client helpers. The per-browser session rides on the server-issued `greg_session` cookie. */

export function apiFetch(url, options = {}) {
  return fetch(url, Object.assign({ credentials: "same-origin" }, options));
}

export async function fetchHealth() {
  const res = await fetch("/health");
  return res.json();
}

export async function fetchViewData(horizonPayload = {}) {
  const params = new URLSearchParams();
  if (horizonPayload.horizon_months != null) {
    params.set("horizon_months", String(horizonPayload.horizon_months));
  }
  if (horizonPayload.horizon_days != null) {
    params.set("horizon", String(horizonPayload.horizon_days));
  }
  const query = params.toString();
  const res = await apiFetch(query ? `/api/view?${query}` : "/api/view");
  return res.json();
}

export function postJson(url, payload) {
  return apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
