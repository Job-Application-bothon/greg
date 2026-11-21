"""Pure-NumPy inference runner for the Seq2Seq Bidirectional-LSTM saved in `planner.keras`."""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class NumpySeq2SeqLSTM:
    """In-memory Keras 3 archive reader & NumPy runner without a TensorFlow dependency."""

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
        base = NumpySeq2SeqLSTM._CLASS_TO_H5_BASE.get(class_name, _camel_to_snake(class_name))
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
    def from_keras_archive(cls, model_path: Path) -> NumpySeq2SeqLSTM:
        """Load layer graph and HDF5 weights from a `.keras` zip archive."""
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
                    if (
                        lcfg.get("activation", "tanh") != "tanh"
                        or lcfg.get("recurrent_activation", "sigmoid") != "sigmoid"
                    ):
                        raise ValueError("Unsupported encoder LSTM activations")
                    if lcfg.get("return_sequences", False) or lcfg.get("go_backwards", False):
                        raise ValueError("Unsupported encoder LSTM sequence flags")
                    enc_weights = cls._read_lstm_weights(
                        grp, int(lcfg["units"]), bool(lcfg.get("use_bias", True))
                    )
                elif cname == "RepeatVector" and repeat_n is None:
                    repeat_name = lname
                    repeat_n = int(lcfg["n"])
                elif cname == "Concatenate" and concat_order is None:
                    concat_axis = int(lcfg.get("axis", -1))
                    inbounds = layer.get("inbound_nodes") or []
                    sources = [
                        str(arg["config"]["keras_history"][0])
                        for arg in inbounds[0].get("args", [[]])[0]
                    ]
                    if len(sources) != 2 or future_input_name not in sources or repeat_name not in sources:
                        raise ValueError(f"Unexpected Concatenate sources: {sources}")
                    concat_order = (
                        "repeat" if sources[0] == repeat_name else "future",
                        "repeat" if sources[1] == repeat_name else "future",
                    )
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
                        if (
                            sub_cfg.get("activation", "tanh") != "tanh"
                            or sub_cfg.get("recurrent_activation", "sigmoid") != "sigmoid"
                        ):
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
        """Run Seq2Seq BiLSTM inference over `[past, future]` numpy arrays."""
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
