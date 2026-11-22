/** Per-tab session isolation and HTTP API client helpers. */

export function getTabSessionId() {
  const key = "greg_tab_session_id";
  let sid = sessionStorage.getItem(key);
  if (!sid) {
    sid =
      window.crypto && typeof window.crypto.randomUUID === "function"
        ? window.crypto.randomUUID()
        : `s-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    sessionStorage.setItem(key, sid);
  }
  return sid;
}

const SESSION_ID = getTabSessionId();

export function apiFetch(url, options = {}) {
  const headers = Object.assign({ "X-Greg-Session": SESSION_ID }, options.headers || {});
  return fetch(url, Object.assign({}, options, { headers }));
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
