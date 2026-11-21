"""Seasonal 12-month plan of money in / money out, built from 1-5 years of history.

How it works (plain words):
1. History is summed per month and cleaned: listed force-majeure periods and one-off spikes/drops are
   replaced by what a normal month would have looked like.
2. A plan is made with one of several methods (see METHODS). "auto" plans each of the business's last
   1-3 years as if unknown with every method and uses the one that came closest. A business too young to
   test gets the neural network (when 24+ months exist), otherwise the classic method.
3. Every plan is kept close to the classic seller method (limits per month and per year), and comes with
   a likely range, cash flow, expected balances and plain-language notes.
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from typing import Any

from greg.config import load_settings
from greg.forecasting.lstm_numpy import NumpySeq2SeqLSTM

METHODS = ("auto", "classic", "network", "ets", "arima", "theta", "combo")
LABELS = {
    "classic": "classic seller method",
    "network": "neural network",
    "ets": "ETS (exponential smoothing)",
    "arima": "ARIMA",
    "theta": "Theta",
    "combo": "average of ETS, ARIMA and Theta",
}
DEFAULTS: dict[str, Any] = {
    "horizon": 12,
    "lookback": 24,
    "lags": [12],
    "classic_years": 3,
    "growth_limits": [0.7, 1.5],
    "exclude_periods": [],
    "outlier_z": 4.0,
    "season_calendar": False,
    "blend_weight_nn": 1.0,
    "band_lo": None,
    "band_hi": None,
    "guardrail": {"max_deviation": 0.25, "max_step_deviation": 0.35, "action": "clip"},
}
DEFAULT_BAND = 0.12
PERIOD = 12

_PLANNER: SeasonalPlanner | None = None
_PLANNER_LOCK = threading.Lock()


def _round2_list(arr: Any) -> list[float]:
    return [round(float(x), 2) for x in arr]


def plan_from_transactions(
    transactions: Iterable[dict[str, Any]],
    method: str = "auto",
    start_balance: float | None = None,
    growth: float | None = None,
    exclude_categories: Iterable[str] = (),
    include_manual: bool = True,
) -> dict[str, Any]:
    """Convert ledger transactions (`date_obj`, signed `amount`) into a JSON-ready 12-month plan."""
    try:
        planner = get_planner()
    except ImportError as exc:
        return {
            "available": False,
            "reason": f"Planner libraries missing ({exc.name}); install the 'planner' extra.",
        }
    flows = monthly_flows(transactions, exclude_categories, include_manual)
    if flows is None:
        return {"available": False, "reason": "No transactions to plan from."}
    return planner.plan(flows, method=method, start_balance=start_balance, growth=growth)


def monthly_flows(
    transactions: Iterable[dict[str, Any]],
    exclude_categories: Iterable[str] = (),
    include_manual: bool = True,
) -> dict[str, Any] | None:
    """Convert signed transactions into positive daily inflow/outflow Pandas series."""
    import pandas as pd

    skip = set(exclude_categories)
    rows = [
        (t["date_obj"], float(t["amount"]))
        for t in transactions
        if t.get("category") not in skip and (include_manual or t.get("source") != "manual")
    ]
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "amount"])
    df["date"] = pd.to_datetime(df["date"])
    daily = df.groupby("date")["amount"]
    return {
        "inflow": daily.apply(lambda a: float(a[a > 0].sum())),
        "outflow": daily.apply(lambda a: float(-a[a < 0].sum())),
    }


def get_planner() -> SeasonalPlanner:
    """Return one shared seasonal planner instance per process (thread-safe lazy load)."""
    global _PLANNER
    with _PLANNER_LOCK:
        if _PLANNER is None:
            _PLANNER = SeasonalPlanner.load()
        return _PLANNER


def reset_planner_cache() -> None:
    """Clear the cached process-level seasonal planner instance (useful in tests)."""
    global _PLANNER
    with _PLANNER_LOCK:
        _PLANNER = None


class SeasonalPlanner:
    """Seasonal 12-month cashflow planner combining classic, neural network, and StatsForecast models."""

    def __init__(self, config: dict[str, Any] | None = None, model: Any = None) -> None:
        import numpy  # noqa: F401  (fail early with ImportError if missing)

        c = {**DEFAULTS, **(config or {})}
        if c.get("freq", "MS") != "MS":
            raise ValueError("Only monthly planner models are supported (freq 'MS').")
        self.H, self.L, self.lags = int(c["horizon"]), int(c["lookback"]), list(c["lags"])
        self.first = max(self.L, max(self.lags))
        self.classic_years = int(c["classic_years"])
        self.growth_limits = tuple(c["growth_limits"])
        self.exclude = [tuple(x) for x in c["exclude_periods"]]
        self.outlier_z = c["outlier_z"]
        self.season_calendar = bool(c["season_calendar"])
        self.guardrail = {**DEFAULTS["guardrail"], **(c.get("guardrail") or {})}
        self.band_lo, self.band_hi = c["band_lo"], c["band_hi"]
        self.model = model
        self.w = float(c["blend_weight_nn"]) if model is not None else 0.0
        self._last_index: dict[str, Any] = {}

    @classmethod
    def load(cls, model_dir: Path | None = None, use_network: bool = True) -> SeasonalPlanner:
        """Load `planner_config.json` and `planner.keras` from the configured model directory."""
        resolved_dir = Path(model_dir) if model_dir is not None else load_settings().planner_model_dir
        cfg_path = resolved_dir / "planner_config.json"
        model_path = resolved_dir / "planner.keras"
        config = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else None
        model = None
        if use_network and config is not None and model_path.exists():
            try:
                model = NumpySeq2SeqLSTM.from_keras_archive(model_path)
            except (ImportError, ValueError):
                os.environ.setdefault("KERAS_BACKEND", "tensorflow")
                os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
                os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
                try:
                    import keras

                    model = keras.models.load_model(model_path)
                except ImportError:
                    model = None
        return cls(config, model)

    def plan(
        self,
        series: dict[str, Any],
        method: str = "auto",
        start_balance: float | None = None,
        growth: float | None = None,
        cash: tuple[str, str] | None = ("inflow", "outflow"),
    ) -> dict[str, Any]:
        """Generate a JSON-ready 12-month plan for each flow series and optional net cash balance."""
        import pandas as pd

        if method not in METHODS:
            raise ValueError(f"Unknown method {method!r}; choose from {METHODS}")
        self._last_index = {}
        out: dict[str, Any] = {
            "available": True,
            "network_loaded": self.model is not None,
            "series": {},
            "notes": [],
        }
        plans = {}
        for name, s in series.items():
            m = self._monthly(s)
            if len(m) < PERIOD:
                return {
                    "available": False,
                    "reason": f"{name}: at least 12 full months of history are needed, got {len(m)}.",
                }
            res, notes = self._plan_one(name, m, method, growth)
            out["series"][name], plans[name] = res, res
            out["notes"] += notes

        last_hist_ts = next(iter(self._last_index.values()))
        months = pd.date_range(last_hist_ts, periods=self.H + 1, freq="MS")[1:]
        out["last_history_month"] = last_hist_ts.strftime("%Y-%m")
        out["months"] = [d.strftime("%Y-%m") for d in months]
        if cash and all(k in plans for k in cash):
            out["cash"] = self._cash(plans[cash[0]], plans[cash[1]], out["months"], start_balance)
        return out

    def _monthly(self, s: Any) -> Any:
        """Convert any series to full calendar months (partial boundary months dropped, empty = 0)."""
        import numpy as np
        import pandas as pd

        s = pd.Series(s).sort_index()
        s.index = pd.to_datetime(s.index)
        if s.empty:
            return s.astype(float)
        lo, hi = s.index.min(), s.index.max()
        unique_idx = s.index.unique()
        if len(unique_idx) > 1:
            dt_secs = np.diff(unique_idx.to_numpy(dtype="datetime64[s]")).astype(np.int64)
            gaps_secs = float(np.median(dt_secs))
        else:
            gaps_secs = 86400.0
        gap_days = gaps_secs / 86400.0
        step_secs = max(gaps_secs - 86400.0, 0.0)
        # Allow 6-day bank-holiday tolerance on daily CSVs, and full-month tolerance on monthly CSVs.
        base_days = 31 if gap_days >= 27.0 else 6
        tol = timedelta(seconds=max(step_secs, base_days * 86400.0))
        m = pd.to_numeric(s, errors="coerce").resample("MS").sum(min_count=1)
        m = m[(m.index >= lo - tol) & (m.index + pd.offsets.MonthEnd(0) <= hi + tol)]
        return m.fillna(0.0).astype(float)

    def _clean(self, s: Any) -> tuple[Any, Any, Any]:
        import numpy as np
        import pandas as pd

        v = s.to_numpy(float)
        exc = np.zeros(len(s), bool)
        for a, b in self.exclude:
            exc |= np.asarray(
                (s.index <= pd.Timestamp(b)) & (s.index + pd.offsets.MonthEnd(0) >= pd.Timestamp(a))
            )
        out = self._outliers(v, exc)
        return self._repair(v, exc | out), exc, out

    def _outliers(self, v: Any, base: Any) -> Any:
        """Detect one-off spikes/drops outside the series' typical YoY change."""
        import numpy as np

        flag = np.zeros(len(v), bool)
        if self.outlier_z is None or len(v) <= 2 * PERIOD:
            return flag
        a, b = v[PERIOD:], v[:-PERIOD]
        ok = (a > 0) & (b > 0) & ~base[PERIOD:] & ~base[:-PERIOD]
        if ok.sum() < 6:
            return flag
        z = np.full(len(v), np.nan)
        r = np.log(a[ok] / b[ok])
        med = np.median(r)
        mad = np.median(np.abs(r - med)) * 1.4826 + 1e-9
        z[PERIOD:][ok] = np.where(np.abs(r - med) > np.log(1.5), (r - med) / mad, 0.0)
        pos_vals = v[v > 0]
        if len(pos_vals) > 0:
            stop = (a == 0) & (b > 0.2 * np.median(pos_vals)) & ~base[PERIOD:] & ~base[:-PERIOD]
            z[PERIOD:][stop] = -np.inf
        flag = np.nan_to_num(np.abs(z)) > self.outlier_z
        for i in range(2 * PERIOD, len(v)):
            if flag[i] and flag[i - PERIOD] and z[i] * z[i - PERIOD] < 0:
                flag[i] = False
        return flag

    def _repair(self, v: Any, mask: Any) -> Any:
        """Replace masked months using the nearest normal year scaled by typical growth."""
        import numpy as np
        import pandas as pd

        v = v.copy()
        if not mask.any():
            return v
        clean, g = ~mask, 1.0
        if len(v) > PERIOD:
            a, b = v[PERIOD:], v[:-PERIOD]
            ok = clean[PERIOD:] & clean[:-PERIOD] & (a > 0) & (b > 0)
            if ok.sum() >= 3:
                g = float(np.exp(np.median(np.log(a[ok] / b[ok]))))
        for t in np.where(mask)[0]:
            for k in (1, -1, 2, -2, 3, -3):
                c = t - k * PERIOD
                if 0 <= c < len(v) and clean[c]:
                    v[t] = v[c] * g**k
                    break
            else:
                v[t] = np.nan
        return pd.Series(v).interpolate(limit_direction="both").fillna(0).to_numpy()

    def _classic(self, v: Any, o: int, growth: float | None = None) -> tuple[Any, float]:
        """Classic seller method: last 12 months times typical yearly growth, spread seasonally."""
        import numpy as np

        last = v[o - PERIOD : o]
        if growth is not None:
            g = 1.0 + growth
        else:
            pairs = [
                np.log(
                    v[o - k * PERIOD : o - (k - 1) * PERIOD].sum()
                    / v[o - (k + 1) * PERIOD : o - k * PERIOD].sum()
                )
                for k in range(1, self.classic_years + 1)
                if o - (k + 1) * PERIOD >= 0
                and v[o - k * PERIOD : o - (k - 1) * PERIOD].sum() > 0
                and v[o - (k + 1) * PERIOD : o - k * PERIOD].sum() > 0
            ]
            g = float(np.clip(np.exp(np.median(pairs)), *self.growth_limits)) if pairs else 1.0
        shapes = [
            v[o - k * PERIOD : o - (k - 1) * PERIOD] / v[o - k * PERIOD : o - (k - 1) * PERIOD].sum()
            for k in range(1, self.classic_years + 1)
            if o - k * PERIOD >= 0 and v[o - k * PERIOD : o - (k - 1) * PERIOD].sum() > 0
        ]
        shape = np.mean(shapes, 0) if shapes else np.full(PERIOD, 1 / PERIOD)
        h = np.arange(self.H)
        fc = shape[h % PERIOD] * last.sum() * g ** (1 + h // PERIOD)
        return np.nan_to_num(fc, nan=0.0, posinf=0.0, neginf=0.0), g

    def _inputs(self, v: Any, start: Any, o: int) -> tuple[list[Any], float]:
        """Build level-scaled history, lags, and classic baseline tensor inputs at origin `o`."""
        import numpy as np
        import pandas as pd

        months = pd.date_range(start, periods=len(v) + self.H, freq="MS").month.to_numpy()
        c_mat = np.eye(12)[months - 1] if self.season_calendar else np.zeros((len(months), 0))
        sc = float(np.abs(v[o - self.L : o]).mean())
        if sc < 1e-6:
            raise ValueError("the last two years are all zero")
        cl, _ = self._classic(v, o)
        past = np.concatenate([v[o - self.L : o, None] / sc, c_mat[o - self.L : o]], 1)
        fut = np.concatenate(
            [c_mat[o : o + self.H]]
            + [v[o - lag : o - lag + self.H, None] / sc for lag in self.lags]
            + [cl[:, None] / sc],
            1,
        )
        return [past[None].astype("float32"), fut[None].astype("float32")], sc

    def _stats(self, v: Any, idx: Any, o: int, nm: str, cache: dict, errors: list) -> Any:
        """Fit a StatsForecast model on history before month `o` -> `(plan, low, high)` or `None`."""
        import numpy as np
        import pandas as pd

        if (o, nm) in cache:
            return cache[(o, nm)]
        res = None
        try:
            from statsforecast import StatsForecast
            from statsforecast.models import AutoARIMA, AutoETS, AutoTheta

            y = np.asarray(v[:o], float)
            if np.all(y == 0):
                cache[(o, nm)] = None
                return None
            model = {"ets": AutoETS, "arima": AutoARIMA, "theta": AutoTheta}[nm](
                season_length=PERIOD if len(y) >= 2 * PERIOD else 1, alias=nm
            )
            fc = StatsForecast(models=[model], freq="MS", n_jobs=1).forecast(
                df=pd.DataFrame({"unique_id": "s", "ds": idx[:o], "y": y}), h=self.H, level=[80]
            )
            fc = fc if "unique_id" in fc.columns else fc.reset_index()
            p = fc[nm].to_numpy(float)
            lo = fc[f"{nm}-lo-80"].to_numpy(float)
            hi = fc[f"{nm}-hi-80"].to_numpy(float)
            if np.isfinite(p).all() and np.isfinite(lo).all() and np.isfinite(hi).all() and len(p) == self.H:
                p_c = p.clip(min=0)
                res = (p_c, np.minimum(lo, p_c).clip(min=0), np.maximum(hi, p_c))
        except ImportError:
            errors.append("StatsForecast is not installed")
        except Exception as exc:
            errors.append(f"{LABELS[nm]} failed: {str(exc)[:80]}")
        cache[(o, nm)] = res
        return res

    def _predict(
        self,
        method: str,
        v: Any,
        idx: Any,
        o: int,
        cache: dict,
        errors: list,
        growth: float | None = None,
    ) -> Any:
        """Produce raw `(plan, low, high)` forecast for one candidate method at origin `o`."""
        import numpy as np

        if method == "combo":
            stats = (self._stats(v, idx, o, nm, cache, errors) for nm in ("ets", "arima", "theta"))
            parts = [x for x in stats if x]
            return tuple(np.mean([x[i] for x in parts], 0) for i in range(3)) if parts else None
        if method in ("ets", "arima", "theta"):
            return self._stats(v, idx, o, method, cache, errors)
        if method == "network":
            if self.model is None or o < self.first:
                return None
            sc_check = float(np.abs(v[o - self.L : o]).mean())
            if sc_check < 1e-6:
                return None
            x_in, sc = self._inputs(v, idx[0], o)
            cl = x_in[1][0, :, -1]
            corr = np.asarray(self.model.predict(x_in, verbose=0)[0], dtype=float)
            p = (self.w * (cl + corr) + (1.0 - self.w) * cl) * sc
        else:
            p = self._classic(v, o, growth)[0]

        p = np.nan_to_num(p, nan=0.0, posinf=0.0, neginf=0.0).clip(min=0)
        sc = float(np.abs(v[max(0, o - self.L) : o]).mean())
        if self.band_lo is not None and o >= self.first:
            lo = (p + np.asarray(self.band_lo, dtype=float) * sc).clip(min=0)
            hi = np.maximum(p + np.asarray(self.band_hi, dtype=float) * sc, p)
            return p, np.minimum(lo, p), hi
        return p, p * (1.0 - DEFAULT_BAND), p * (1.0 + DEFAULT_BAND)

    def _choose(
        self,
        name: str,
        v: Any,
        idx: Any,
        candidates: list[str],
        cache: dict,
        errors: list,
    ) -> tuple[str, str]:
        """Backtest each of the last 1-3 years and select the method with lowest forecast error."""
        import numpy as np

        n = len(v)
        origins = [n - k * self.H for k in (1, 2, 3) if n - k * self.H >= max(self.first, 2 * PERIOD)]
        if not origins:
            if "network" in candidates and float(np.abs(v[n - self.L : n]).mean()) >= 1e-6:
                return "network", (
                    f"{name}: too little history to compare methods -> neural network used (it learned "
                    "from thousands of businesses, e.g. that fast growth usually slows down)"
                )
            return "classic", f"{name}: too little history to compare methods -> classic seller method used"

        scores: dict[str, float] = {}
        for m in candidates:
            errs = []
            for o in origins:
                a = v[o : o + self.H]
                a_sum = float(a.sum())
                if a_sum <= 1e-9:
                    continue
                r = self._predict(m, v, idx, o, cache, errors)
                if r is None:
                    errs = []
                    break
                p = r[0]
                errs.append((abs(float(p.sum()) - a_sum) / a_sum + float(np.abs(p - a).sum()) / a_sum) / 2.0)
            if errs:
                scores[m] = float(np.mean(errs))

        if not scores:
            return (
                "classic",
                f"{name}: insufficient non-zero history to backtest methods -> classic seller method used",
            )

        best = min(scores, key=lambda k: scores[k])
        if "classic" in scores and best != "classic" and scores[best] > scores["classic"] - 0.01:
            best = "classic"
        ranking = ", ".join(
            f"{LABELS[m].split(' (')[0]} {e:.1%}" for m, e in sorted(scores.items(), key=lambda x: x[1])
        )
        k = len(origins)
        return best, (
            f"{name}: {LABELS[best]} chosen automatically. Average error when planning the last {k} "
            f"year{'s' if k > 1 else ''} as if unknown: {ranking}"
        )

    def _limit(self, name: str, fc: Any, cl: Any) -> tuple[Any, list[str]]:
        """Keep a forecast within configured per-month and annual bounds of the classic baseline."""
        import numpy as np

        gr, notes = self.guardrail, []
        clip = gr.get("action") == "clip"
        fc = np.nan_to_num(fc, nan=0.0, posinf=0.0, neginf=0.0).clip(min=0)
        sd, md = gr.get("max_step_deviation"), gr.get("max_deviation")
        if sd is not None:
            lo, hi = cl * (1.0 - sd), cl * (1.0 + sd)
            k = int(((fc < lo) | (fc > hi)).sum())
            if k:
                notes.append(
                    f"{name}: {k} month(s) differ from the classic plan by more than +/-{sd:.0%}"
                    + (" -> limited" if clip else "")
                )
                if clip:
                    fc = np.clip(fc, lo, hi)
        cl_sum = float(cl.sum())
        fc_sum = float(fc.sum())
        dev = fc_sum / cl_sum - 1.0 if cl_sum > 1e-9 else 0.0
        if md is not None and abs(dev) > md and cl_sum > 1e-9 and fc_sum > 1e-9:
            notes.append(
                f"{name}: plan total is {dev:+.0%} vs classic plan (limit +/-{md:.0%})"
                + (" -> scaled back to the limit" if clip else "")
            )
            if clip:
                fc = fc * (1.0 + float(np.sign(dev)) * md) * cl_sum / fc_sum
        return fc, notes

    def _plan_one(
        self, name: str, s: Any, method: str, growth: float | None
    ) -> tuple[dict[str, Any], list[str]]:
        import numpy as np

        v, exc, out = self._clean(s)
        n, idx, notes, cache, errors = len(v), s.index, [], {}, []
        self._last_index[name] = idx[-1]
        if (exc | out).any():
            notes.append(
                f"{name}: replaced {int(exc.sum())} excluded and {int(out.sum())} unusual month(s): "
                + ", ".join(idx[out | exc].strftime("%Y-%m"))
            )
        if n < self.first:
            notes.append(
                f"{name}: only {n} months of history -> classic seller method, "
                + ("growth as set by you" if growth is not None else "no growth assumed")
            )
            method = "classic"
        if growth is not None and method == "auto":
            method = "classic"
        if method == "auto":
            cands = ["classic"] + (["network"] if self.model is not None else [])
            try:
                import statsforecast  # noqa: F401

                cands += ["ets", "arima", "theta", "combo"]
            except ImportError:
                notes.append(f"{name}: StatsForecast not installed -> only classic/network compared")
            method, why = self._choose(name, v, idx, cands, cache, errors)
            notes.append(why)

        cl, g = self._classic(v, n, growth)
        r = self._predict(method, v, idx, n, cache, errors, growth)
        if r is None:
            notes += [f"{name}: {e}" for e in errors[-2:]]
            notes.append(f"{name}: {LABELS[method]} could not make a plan -> classic seller method used")
            method = "classic"
            r = self._predict("classic", v, idx, n, cache, errors, growth)

        raw, lo, hi = r
        fc, lim = (raw.clip(min=0), []) if method == "classic" else self._limit(name, raw, cl)
        notes += lim
        ratio = np.divide(fc, raw, out=np.ones_like(fc), where=raw > 1e-9)
        lo = np.nan_to_num(np.minimum(lo * ratio, fc), nan=0.0, posinf=0.0, neginf=0.0).clip(min=0)
        hi = np.nan_to_num(np.maximum(hi * ratio, fc), nan=0.0, posinf=0.0, neginf=0.0)
        fc = np.nan_to_num(fc, nan=0.0, posinf=0.0, neginf=0.0)
        if g in self.growth_limits and growth is None and method == "classic":
            notes.append(f"{name}: growth capped at {g - 1:+.0%}/yr - the history suggests more")

        prev12 = float(v[-PERIOD:].sum())
        yoy_str = f"{float(fc.sum()) / prev12 - 1.0:+.1%} vs last 12" if prev12 > 1e-9 else "n/a vs last 12"
        notes.append(
            f"{name}: {LABELS[method]} | next 12 months {float(fc.sum()):,.0f} "
            f"({yoy_str}) | classic growth {g - 1:+.1%}/yr"
        )
        hist = slice(max(0, n - 2 * PERIOD), n)
        return {
            "method": method,
            "method_label": LABELS[method],
            "plan": _round2_list(fc),
            "low": _round2_list(lo),
            "high": _round2_list(hi),
            "classic": _round2_list(cl),
            "classic_growth": round(g - 1, 4),
            "history": {
                "months": [d.strftime("%Y-%m") for d in idx[hist]],
                "actual": _round2_list(s.to_numpy()[hist]),
                "cleaned": _round2_list(v[hist]),
            },
            "flagged_months": [d.strftime("%Y-%m") for d in idx[out | exc]],
        }, notes

    @staticmethod
    def _cash(inflow: dict, outflow: dict, months: list[str], start: float | None) -> dict[str, Any]:
        """Compute monthly net cash flow and expected/pessimistic running balances."""
        import numpy as np

        net = np.array(inflow["plan"], dtype=float) - np.array(outflow["plan"], dtype=float)
        net_p = np.array(inflow["low"], dtype=float) - np.array(outflow["high"], dtype=float)
        base = float(start or 0.0)
        bal, bal_p = base + np.cumsum(net), base + np.cumsum(net_p)

        def _lowest_point(series: Any) -> dict[str, Any]:
            return {"month": months[int(np.argmin(series))], "balance": round(float(np.min(series)), 2)}

        return {
            "start_balance": round(base, 2),
            "net": _round2_list(net),
            "net_pessimistic": _round2_list(net_p),
            "balance": _round2_list(bal),
            "balance_pessimistic": _round2_list(bal_p),
            "lowest": _lowest_point(bal),
            "lowest_pessimistic": _lowest_point(bal_p),
        }
