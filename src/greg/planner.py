"""Seasonal 12-month plan of money in / money out, built from 1–5 years of history.

How it works (plain words):
1. History is summed per month and cleaned: listed force-majeure periods and one-off spikes/drops are
   replaced by what a normal month would have looked like.
2. A plan is made with one of several methods (see METHODS). "auto" plans each of the business's last
   1–3 years as if unknown with every method and uses the one that came closest. A business too young to
   test gets the neural network (when 24+ months exist), otherwise the classic method.
3. Every plan is kept close to the classic seller method (limits per month and per year), and comes with
   a likely range, cash flow, expected balances and plain-language notes.

Needs numpy + pandas (+ h5py for loading planner.keras in pure NumPy without TensorFlow).
Optional: statsforecast (ETS / ARIMA / Theta) and keras (fallback if model uses unsupported layers).
The trained model lives in planner_model/ (planner.keras + planner_config.json).
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import zipfile
from pathlib import Path
from typing import Any, Iterable

MODEL_DIR = Path(os.environ.get("GREG_PLANNER_DIR", Path(__file__).resolve().parent / "planner_model"))
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
P = 12

_PLANNER: "Planner | None" = None
_PLANNER_LOCK = threading.Lock()


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class _NumpySeq2SeqLSTM:
    """Pure-NumPy inference runner for the Seq2Seq Bidirectional-LSTM saved in `planner.keras`.

    Reads `config.json` and `model.weights.h5` completely in memory using `zipfile` + `h5py`
    so TensorFlow does not need to be installed at runtime.
    """

    _CLASS_TO_H5_BASE = {
        "InputLayer": "input_layer",
        "LSTM": "lstm",
        "RepeatVector": "repeat_vector",
        "Concatenate": "concatenate",
        "Bidirectional": "bidirectional",
        "Dropout": "dropout",
        "Dense": "dense",
        "Flatten": "flatten",
    }

    def __init__(
        self,
        enc_weights: tuple[Any, Any, Any | None],
        repeat_n: int,
        concat_order: tuple[str, str],
        concat_axis: int,
        dec_fwd_weights: tuple[Any, Any, Any | None],
        dec_bwd_weights: tuple[Any, Any, Any | None],
        dense_layers: list[tuple[Any, Any | None, str]],
    ) -> None:
        self.enc_weights = enc_weights
        self.repeat_n = repeat_n
        self.concat_order = concat_order
        self.concat_axis = concat_axis
        self.dec_fwd_weights = dec_fwd_weights
        self.dec_bwd_weights = dec_bwd_weights
        self.dense_layers = dense_layers

    @staticmethod
    def _resolve_layer_group(
        layers_grp: Any,
        layer_name: str,
        class_name: str,
        counters: dict[str, int],
    ) -> Any:
        base = _NumpySeq2SeqLSTM._CLASS_TO_H5_BASE.get(class_name, _camel_to_snake(class_name))
        idx = counters.get(base, 0)
        counters[base] = idx + 1
        auto_name = base if idx == 0 else f"{base}_{idx}"
        if layer_name in layers_grp:
            return layers_grp[layer_name]
        if auto_name in layers_grp:
            return layers_grp[auto_name]
        raise ValueError(
            f"Layer {layer_name!r} (auto {auto_name!r}) not found in HDF5 weights: {list(layers_grp.keys())}"
        )

    @staticmethod
    def _read_lstm_weights(cell_parent: Any, units: int, use_bias: bool) -> tuple[Any, Any, Any | None]:
        import numpy as np

        vars_grp = cell_parent["cell"]["vars"] if "cell" in cell_parent else cell_parent["vars"]
        kernel = np.asarray(vars_grp["0"], dtype=np.float32)
        rec_kernel = np.asarray(vars_grp["1"], dtype=np.float32)
        bias = np.asarray(vars_grp["2"], dtype=np.float32) if (use_bias and "2" in vars_grp) else None
        if kernel.ndim != 2 or kernel.shape[1] != 4 * units:
            raise ValueError(f"Unexpected LSTM kernel shape {kernel.shape} for units={units}")
        if rec_kernel.shape != (units, 4 * units):
            raise ValueError(f"Unexpected LSTM recurrent shape {rec_kernel.shape} for units={units}")
        if bias is not None and bias.shape != (4 * units,):
            raise ValueError(f"Unexpected LSTM bias shape {bias.shape} for units={units}")
        return kernel, rec_kernel, bias

    @classmethod
    def from_keras_archive(cls, model_path: Path) -> "_NumpySeq2SeqLSTM":
        import h5py
        import numpy as np

        with zipfile.ZipFile(model_path, "r") as zf:
            cfg = json.loads(zf.read("config.json").decode("utf-8"))
            h5_bytes = zf.read("model.weights.h5")

        if cfg.get("class_name") != "Functional":
            raise ValueError(f"Unsupported Keras model class: {cfg.get('class_name')!r}")

        model_cfg = cfg.get("config") or {}
        layers_cfg = model_cfg.get("layers") or []
        input_layers = model_cfg.get("input_layers") or []
        if len(input_layers) != 2:
            raise ValueError(f"Expected 2 model inputs (past, future), got {len(input_layers)}")
        past_input_name = str(input_layers[0][0])
        future_input_name = str(input_layers[1][0])

        with h5py.File(io.BytesIO(h5_bytes), "r") as hf:
            layers_grp = hf["layers"]
            counters: dict[str, int] = {}

            enc_weights = None
            repeat_name = None
            repeat_n = None
            concat_order = None
            concat_axis = -1
            dec_fwd_weights = None
            dec_bwd_weights = None
            dense_layers: list[tuple[Any, Any | None, str]] = []

            for layer in layers_cfg:
                cname = layer.get("class_name")
                lname = str(layer.get("name"))
                lcfg = layer.get("config") or {}
                grp = cls._resolve_layer_group(layers_grp, lname, str(cname), counters)

                if cname == "InputLayer":
                    continue
                if cname == "LSTM" and enc_weights is None:
                    if lcfg.get("activation", "tanh") != "tanh" or lcfg.get("recurrent_activation", "sigmoid") != "sigmoid":
                        raise ValueError("Unsupported encoder LSTM activations")
                    if lcfg.get("return_sequences", False) or lcfg.get("go_backwards", False):
                        raise ValueError("Unsupported encoder LSTM sequence flags")
                    enc_weights = cls._read_lstm_weights(grp, int(lcfg["units"]), bool(lcfg.get("use_bias", True)))
                elif cname == "RepeatVector" and repeat_n is None:
                    repeat_name = lname
                    repeat_n = int(lcfg["n"])
                elif cname == "Concatenate" and concat_order is None:
                    concat_axis = int(lcfg.get("axis", -1))
                    inbounds = layer.get("inbound_nodes") or []
                    sources = []
                    for arg in inbounds[0].get("args", [[]])[0]:
                        sources.append(str(arg["config"]["keras_history"][0]))
                    if len(sources) != 2 or future_input_name not in sources or repeat_name not in sources:
                        raise ValueError(f"Unexpected Concatenate sources: {sources}")
                    concat_order = ("repeat" if sources[0] == repeat_name else "future",
                                    "repeat" if sources[1] == repeat_name else "future")
                elif cname == "Bidirectional" and dec_fwd_weights is None:
                    if lcfg.get("merge_mode", "concat") != "concat":
                        raise ValueError(f"Unsupported Bidirectional merge_mode: {lcfg.get('merge_mode')!r}")
                    fwd_cfg = (lcfg.get("layer") or {}).get("config") or {}
                    bwd_cfg = (lcfg.get("backward_layer") or {}).get("config") or {}
                    if not fwd_cfg.get("return_sequences", False) or fwd_cfg.get("go_backwards", False):
                        raise ValueError("Unsupported forward decoder LSTM flags")
                    if not bwd_cfg.get("return_sequences", False) or not bwd_cfg.get("go_backwards", False):
                        raise ValueError("Unsupported backward decoder LSTM flags")
                    for sub_cfg in (fwd_cfg, bwd_cfg):
                        if sub_cfg.get("activation", "tanh") != "tanh" or sub_cfg.get("recurrent_activation", "sigmoid") != "sigmoid":
                            raise ValueError("Unsupported decoder LSTM activations")
                    dec_fwd_weights = cls._read_lstm_weights(
                        grp["forward_layer"], int(fwd_cfg["units"]), bool(fwd_cfg.get("use_bias", True))
                    )
                    dec_bwd_weights = cls._read_lstm_weights(
                        grp["backward_layer"], int(bwd_cfg["units"]), bool(bwd_cfg.get("use_bias", True))
                    )
                elif cname == "Dropout":
                    continue
                elif cname == "Dense":
                    vars_grp = grp["vars"]
                    d_w = np.asarray(vars_grp["0"], dtype=np.float32)
                    d_b = (
                        np.asarray(vars_grp["1"], dtype=np.float32)
                        if (bool(lcfg.get("use_bias", True)) and "1" in vars_grp)
                        else None
                    )
                    act = str(lcfg.get("activation", "linear"))
                    if act not in ("linear", "relu", "tanh", "sigmoid"):
                        raise ValueError(f"Unsupported Dense activation: {act!r}")
                    dense_layers.append((d_w, d_b, act))
                elif cname == "Flatten":
                    continue
                else:
                    raise ValueError(f"Unsupported Keras layer in planner.keras: {cname!r} ({lname!r})")

        if (
            past_input_name is None
            or enc_weights is None
            or repeat_n is None
            or concat_order is None
            or dec_fwd_weights is None
            or dec_bwd_weights is None
            or not dense_layers
        ):
            raise ValueError("Incomplete Seq2Seq-BiLSTM graph in planner.keras")

        return cls(
            enc_weights=enc_weights,
            repeat_n=repeat_n,
            concat_order=concat_order,
            concat_axis=concat_axis,
            dec_fwd_weights=dec_fwd_weights,
            dec_bwd_weights=dec_bwd_weights,
            dense_layers=dense_layers,
        )

    @staticmethod
    def _sigmoid(x: Any) -> Any:
        import numpy as np

        return 1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))

    @classmethod
    def _run_lstm(
        cls,
        x: Any,
        weights: tuple[Any, Any, Any | None],
        *,
        return_sequences: bool,
        go_backwards: bool,
    ) -> Any:
        import numpy as np

        kernel, rec_kernel, bias = weights
        batch_size, steps, _ = x.shape
        units = rec_kernel.shape[0]
        h = np.zeros((batch_size, units), dtype=np.float32)
        c = np.zeros((batch_size, units), dtype=np.float32)
        out_seq = np.empty((batch_size, steps, units), dtype=np.float32) if return_sequences else None

        indices = range(steps - 1, -1, -1) if go_backwards else range(steps)
        for step_out_idx, t in enumerate(indices):
            z = x[:, t, :] @ kernel + h @ rec_kernel
            if bias is not None:
                z = z + bias
            # Keras LSTM gate order is [i, f, c_candidate, o]
            i_g, f_g, c_g, o_g = np.split(z, 4, axis=-1)
            i_act = cls._sigmoid(i_g)
            f_act = cls._sigmoid(f_g)
            c_cand = np.tanh(c_g)
            o_act = cls._sigmoid(o_g)
            c = f_act * c + i_act * c_cand
            h = o_act * np.tanh(c)
            if return_sequences and out_seq is not None:
                out_seq[:, step_out_idx, :] = h

        return out_seq if return_sequences else h

    def predict(self, inputs: list[Any], verbose: int = 0) -> Any:  # noqa: ARG002
        import numpy as np

        past = np.asarray(inputs[0], dtype=np.float32)
        fut = np.asarray(inputs[1], dtype=np.float32)

        enc_out = self._run_lstm(past, self.enc_weights, return_sequences=False, go_backwards=False)
        rep = np.repeat(enc_out[:, None, :], self.repeat_n, axis=1)

        parts = [rep if slot == "repeat" else fut for slot in self.concat_order]
        dec_in = np.concatenate(parts, axis=self.concat_axis)

        fwd = self._run_lstm(dec_in, self.dec_fwd_weights, return_sequences=True, go_backwards=False)
        # Bidirectional reverses the go_backwards output sequence back to chronological order before concat
        bwd_rev = self._run_lstm(dec_in, self.dec_bwd_weights, return_sequences=True, go_backwards=True)
        bwd = bwd_rev[:, ::-1, :]
        x = np.concatenate([fwd, bwd], axis=-1)

        for d_w, d_b, act in self.dense_layers:
            x = x @ d_w
            if d_b is not None:
                x = x + d_b
            if act == "relu":
                x = np.maximum(x, 0.0)
            elif act == "tanh":
                x = np.tanh(x)
            elif act == "sigmoid":
                x = self._sigmoid(x)

        return x.reshape((x.shape[0], -1)).astype(np.float32)


def plan_from_transactions(
    transactions: Iterable[dict[str, Any]],
    method: str = "auto",
    start_balance: float | None = None,
    growth: float | None = None,
    exclude_categories: Iterable[str] = (),
    include_manual: bool = True,
) -> dict[str, Any]:
    """Ledger transactions (dicts with 'date_obj' and signed 'amount') → JSON-ready 12-month plan.

    growth: optional yearly growth the owner wants assumed for the classic plan, e.g. 0.05 for +5 %.
    """
    try:
        planner = get_planner()
    except ImportError as e:
        return {"available": False, "reason": f"Planner libraries missing ({e.name}); install the 'planner' extra."}
    flows = monthly_flows(transactions, exclude_categories, include_manual)
    if flows is None:
        return {"available": False, "reason": "No transactions to plan from."}
    return planner.plan(flows, method=method, start_balance=start_balance, growth=growth)


def monthly_flows(
    transactions: Iterable[dict[str, Any]],
    exclude_categories: Iterable[str] = (),
    include_manual: bool = True,
) -> dict[str, Any] | None:
    """Signed transactions → {'inflow': daily series, 'outflow': daily series} (both positive amounts)."""
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


def get_planner() -> "Planner":
    """One shared planner per process (thread-safe lazy load)."""
    global _PLANNER
    with _PLANNER_LOCK:
        if _PLANNER is None:
            _PLANNER = Planner.load()
        return _PLANNER


class Planner:
    def __init__(self, config: dict[str, Any] | None = None, model: Any = None) -> None:
        import numpy  # noqa: F401  (fail early with ImportError if missing)

        c = {**DEFAULTS, **(config or {})}
        if c.get("freq", "MS") != "MS":
            raise ValueError("Only monthly planner models are supported (freq 'MS').")
        self.H, self.L, self.lags = int(c["horizon"]), int(c["lookback"]), list(c["lags"])
        self.first = max(self.L, max(self.lags))
        self.classic_years, self.growth_limits = int(c["classic_years"]), tuple(c["growth_limits"])
        self.exclude = [tuple(x) for x in c["exclude_periods"]]
        self.outlier_z, self.season_calendar = c["outlier_z"], bool(c["season_calendar"])
        self.guardrail = {**DEFAULTS["guardrail"], **(c.get("guardrail") or {})}
        self.band_lo, self.band_hi = c["band_lo"], c["band_hi"]
        self.model = model
        self.w = float(c["blend_weight_nn"]) if model is not None else 0.0
        self._last_index: dict[str, Any] = {}

    @classmethod
    def load(cls, model_dir: Path = MODEL_DIR, use_network: bool = True) -> "Planner":
        cfg_path = Path(model_dir) / "planner_config.json"
        model_path = Path(model_dir) / "planner.keras"
        config = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else None
        model = None
        if use_network and config is not None and model_path.exists():
            try:
                model = _NumpySeq2SeqLSTM.from_keras_archive(model_path)
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

    # ---------- public ----------
    def plan(
        self,
        series: dict[str, Any],
        method: str = "auto",
        start_balance: float | None = None,
        growth: float | None = None,
        cash: tuple[str, str] | None = ("inflow", "outflow"),
    ) -> dict[str, Any]:
        """{name: pandas Series with a DatetimeIndex (any frequency)} → JSON-ready plan for every series."""
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
            if len(m) < P:
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

    # ---------- data preparation ----------
    def _monthly(self, s: Any) -> Any:
        """Any series → full calendar months (partial first/last month dropped, empty months = 0)."""
        import pandas as pd

        s = pd.Series(s).sort_index()
        s.index = pd.to_datetime(s.index)
        if s.empty:
            return s.astype(float)
        lo, hi = s.index.min(), s.index.max()
        gaps = pd.Series(s.index.unique()).diff().median() if s.index.nunique() > 1 else pd.Timedelta(days=1)
        step = max(gaps, pd.Timedelta(days=1)) - pd.Timedelta(days=1)
        # Allow 6-day bank-holiday tolerance on daily CSVs, and full-month tolerance on monthly CSVs (27+ day spacing)
        tol = max(step, pd.Timedelta(days=31) if gaps >= pd.Timedelta(days=27) else pd.Timedelta(days=6))
        m = pd.to_numeric(s, errors="coerce").resample("MS").sum(min_count=1)
        m = m[(m.index >= lo - tol) & (m.index + pd.offsets.MonthEnd(0) <= hi + tol)]
        return m.fillna(0.0).astype(float)

    def _clean(self, s: Any) -> tuple[Any, Any, Any]:
        import numpy as np
        import pandas as pd

        v = s.to_numpy(float)
        exc = np.zeros(len(s), bool)
        for a, b in self.exclude:
            exc |= np.asarray((s.index <= pd.Timestamp(b)) & (s.index + pd.offsets.MonthEnd(0) >= pd.Timestamp(a)))
        out = self._outliers(v, exc)
        return self._repair(v, exc | out), exc, out

    def _outliers(self, v: Any, base: Any) -> Any:
        """One-off spikes/drops: year-over-year change far outside this series' usual changes (≥50 %)."""
        import numpy as np

        flag = np.zeros(len(v), bool)
        if self.outlier_z is None or len(v) <= 2 * P:
            return flag
        a, b = v[P:], v[:-P]
        ok = (a > 0) & (b > 0) & ~base[P:] & ~base[:-P]
        if ok.sum() < 6:
            return flag
        z = np.full(len(v), np.nan)
        r = np.log(a[ok] / b[ok])
        med = np.median(r)
        mad = np.median(np.abs(r - med)) * 1.4826 + 1e-9
        z[P:][ok] = np.where(np.abs(r - med) > np.log(1.5), (r - med) / mad, 0.0)
        pos_vals = v[v > 0]
        if len(pos_vals) > 0:
            stop = (a == 0) & (b > 0.2 * np.median(pos_vals)) & ~base[P:] & ~base[:-P]
            z[P:][stop] = -np.inf
        flag = np.nan_to_num(np.abs(z)) > self.outlier_z
        for i in range(2 * P, len(v)):
            if flag[i] and flag[i - P] and z[i] * z[i - P] < 0:
                flag[i] = False
        return flag

    def _repair(self, v: Any, mask: Any) -> Any:
        """Masked months ← same month of the nearest normal year, adjusted by typical growth."""
        import numpy as np
        import pandas as pd

        v = v.copy()
        if not mask.any():
            return v
        clean, g = ~mask, 1.0
        if len(v) > P:
            a, b = v[P:], v[:-P]
            ok = clean[P:] & clean[:-P] & (a > 0) & (b > 0)
            if ok.sum() >= 3:
                g = float(np.exp(np.median(np.log(a[ok] / b[ok]))))
        for t in np.where(mask)[0]:
            for k in (1, -1, 2, -2, 3, -3):
                c = t - k * P
                if 0 <= c < len(v) and clean[c]:
                    v[t] = v[c] * g**k
                    break
            else:
                v[t] = np.nan
        return pd.Series(v).interpolate(limit_direction="both").fillna(0).to_numpy()

    # ---------- methods ----------
    def _classic(self, v: Any, o: int, growth: float | None = None) -> tuple[Any, float]:
        """Seller's method: last 12 months × typical yearly growth, spread by average seasonal shares."""
        import numpy as np

        last = v[o - P : o]
        if growth is not None:
            g = 1.0 + growth
        else:
            pairs = [
                np.log(v[o - k * P : o - (k - 1) * P].sum() / v[o - (k + 1) * P : o - k * P].sum())
                for k in range(1, self.classic_years + 1)
                if o - (k + 1) * P >= 0
                and v[o - k * P : o - (k - 1) * P].sum() > 0
                and v[o - (k + 1) * P : o - k * P].sum() > 0
            ]
            g = float(np.clip(np.exp(np.median(pairs)), *self.growth_limits)) if pairs else 1.0
        shapes = [
            v[o - k * P : o - (k - 1) * P] / v[o - k * P : o - (k - 1) * P].sum()
            for k in range(1, self.classic_years + 1)
            if o - k * P >= 0 and v[o - k * P : o - (k - 1) * P].sum() > 0
        ]
        shape = np.mean(shapes, 0) if shapes else np.full(P, 1 / P)
        h = np.arange(self.H)
        return np.nan_to_num(shape[h % P] * last.sum() * g ** (1 + h // P), nan=0.0, posinf=0.0, neginf=0.0), g

    def _inputs(self, v: Any, start: Any, o: int) -> tuple[list[Any], float]:
        """Network inputs at origin o (level-scaled history, lags, classic plan)."""
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
        """One StatsForecast model on the history before month o → (plan, low, high) or None."""
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
                season_length=P if len(y) >= 2 * P else 1, alias=nm
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
        except Exception as e:
            errors.append(f"{LABELS[nm]} failed: {str(e)[:80]}")
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
        """Raw plan of one method from the history before month o → (plan, low, high) or None."""
        import numpy as np

        if method == "combo":
            parts = [x for x in (self._stats(v, idx, o, nm, cache, errors) for nm in ("ets", "arima", "theta")) if x]
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
        """Plan each of the last 1–3 years as if unknown; pick the method closest to what happened."""
        import numpy as np

        n = len(v)
        origins = [n - k * self.H for k in (1, 2, 3) if n - k * self.H >= max(self.first, 2 * P)]
        if not origins:
            if "network" in candidates and float(np.abs(v[n - self.L : n]).mean()) >= 1e-6:
                return "network", (
                    f"{name}: too little history to compare methods → neural network used (it learned "
                    "from thousands of businesses, e.g. that fast growth usually slows down)"
                )
            return "classic", f"{name}: too little history to compare methods → classic seller method used"

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
            return "classic", f"{name}: insufficient non-zero history to backtest methods → classic seller method used"

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
        """Keep a plan close to the classic plan, per month and for the year."""
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
                    f"{name}: {k} month(s) differ from the classic plan by more than ±{sd:.0%}"
                    + (" → limited" if clip else "")
                )
                if clip:
                    fc = np.clip(fc, lo, hi)
        cl_sum = float(cl.sum())
        fc_sum = float(fc.sum())
        dev = fc_sum / cl_sum - 1.0 if cl_sum > 1e-9 else 0.0
        if md is not None and abs(dev) > md and cl_sum > 1e-9 and fc_sum > 1e-9:
            notes.append(
                f"{name}: plan total is {dev:+.0%} vs classic plan (limit ±{md:.0%})"
                + (" → scaled back to the limit" if clip else "")
            )
            if clip:
                fc = fc * (1.0 + float(np.sign(dev)) * md) * cl_sum / fc_sum
        return fc, notes

    def _plan_one(self, name: str, s: Any, method: str, growth: float | None) -> tuple[dict[str, Any], list[str]]:
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
                f"{name}: only {n} months of history → classic seller method, "
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
                notes.append(f"{name}: StatsForecast not installed → only classic/network compared")
            method, why = self._choose(name, v, idx, cands, cache, errors)
            notes.append(why)

        cl, g = self._classic(v, n, growth)
        r = self._predict(method, v, idx, n, cache, errors, growth)
        if r is None:
            notes += [f"{name}: {e}" for e in errors[-2:]]
            notes.append(f"{name}: {LABELS[method]} could not make a plan → classic seller method used")
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
            notes.append(f"{name}: growth capped at {g - 1:+.0%}/yr — the history suggests more")

        prev12 = float(v[-P:].sum())
        yoy_str = f"{float(fc.sum()) / prev12 - 1.0:+.1%} vs last 12" if prev12 > 1e-9 else "n/a vs last 12"
        notes.append(
            f"{name}: {LABELS[method]} | next 12 months {float(fc.sum()):,.0f} "
            f"({yoy_str}) | classic growth {g - 1:+.1%}/yr"
        )
        hist = slice(max(0, n - 2 * P), n)
        r2 = lambda a: [round(float(x), 2) for x in a]
        return {
            "method": method,
            "method_label": LABELS[method],
            "plan": r2(fc),
            "low": r2(lo),
            "high": r2(hi),
            "classic": r2(cl),
            "classic_growth": round(g - 1, 4),
            "history": {
                "months": [d.strftime("%Y-%m") for d in idx[hist]],
                "actual": r2(s.to_numpy()[hist]),
                "cleaned": r2(v[hist]),
            },
            "flagged_months": [d.strftime("%Y-%m") for d in idx[out | exc]],
        }, notes

    @staticmethod
    def _cash(inflow: dict, outflow: dict, months: list[str], start: float | None) -> dict[str, Any]:
        """Monthly net cash flow and running balance (expected and pessimistic: low inflow + high outflow)."""
        import numpy as np

        net = np.array(inflow["plan"], dtype=float) - np.array(outflow["plan"], dtype=float)
        net_p = np.array(inflow["low"], dtype=float) - np.array(outflow["high"], dtype=float)
        base = float(start or 0.0)
        bal, bal_p = base + np.cumsum(net), base + np.cumsum(net_p)
        r2 = lambda a: [round(float(x), 2) for x in a]
        low = lambda b: {"month": months[int(np.argmin(b))], "balance": round(float(np.min(b)), 2)}
        return {
            "start_balance": round(base, 2),
            "net": r2(net),
            "net_pessimistic": r2(net_p),
            "balance": r2(bal),
            "balance_pessimistic": r2(bal_p),
            "lowest": low(bal),
            "lowest_pessimistic": low(bal_p),
        }
