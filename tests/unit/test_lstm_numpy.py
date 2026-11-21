"""Unit tests verifying `NumpySeq2SeqLSTM` archive extraction and numerical math parity."""
from __future__ import annotations

import io
import math
import unittest
import zipfile
from pathlib import Path

from greg.forecasting.lstm_numpy import NumpySeq2SeqLSTM

MODEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "greg"
    / "forecasting"
    / "models"
    / "planner.keras"
)


class PlannerNumpyLSTMTest(unittest.TestCase):
    def test_numpy_lstm_loads_and_matches_reference_step_math(self) -> None:
        import h5py
        import numpy as np

        self.assertTrue(MODEL_PATH.exists(), f"Missing model file at {MODEL_PATH}")
        runner = NumpySeq2SeqLSTM.from_keras_archive(MODEL_PATH)

        self.assertEqual(runner.enc_weights[0].shape, (1, 256))
        self.assertEqual(runner.enc_weights[1].shape, (64, 256))
        self.assertEqual(runner.enc_weights[2].shape, (256,))
        self.assertEqual(runner.repeat_n, 12)
        self.assertEqual(runner.dec_fwd_weights[0].shape, (66, 128))
        self.assertEqual(runner.dec_bwd_weights[0].shape, (66, 128))
        self.assertEqual(len(runner.dense_layers), 2)
        self.assertEqual(runner.dense_layers[0][0].shape, (64, 32))
        self.assertEqual(runner.dense_layers[1][0].shape, (32, 1))

        rng = np.random.default_rng(42)
        past = rng.normal(1.0, 0.2, size=(2, 24, 1)).astype(np.float32)
        fut = rng.normal(1.0, 0.15, size=(2, 12, 2)).astype(np.float32)
        pred = runner.predict([past, fut], verbose=0)

        self.assertEqual(pred.shape, (2, 12))
        self.assertTrue(np.isfinite(pred).all())
        self.assertGreater(float(np.abs(pred).max()), 1e-4)

        with zipfile.ZipFile(MODEL_PATH, "r") as zf:
            hf_bytes = zf.read("model.weights.h5")

        def sig(x: float) -> float:
            return float(1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, float(x))))))

        def ref_lstm_step_seq(seq: np.ndarray, grp: h5py.Group, go_backwards: bool) -> np.ndarray:
            w = np.asarray(grp["cell/vars/0"], dtype=np.float32)
            u = np.asarray(grp["cell/vars/1"], dtype=np.float32)
            b = np.asarray(grp["cell/vars/2"], dtype=np.float32)
            units = u.shape[0]
            bsz, steps, _ = seq.shape
            outs = np.zeros((bsz, steps, units), dtype=np.float32)
            for bi in range(bsz):
                h = np.zeros(units, dtype=np.float32)
                c = np.zeros(units, dtype=np.float32)
                order = list(range(steps - 1, -1, -1)) if go_backwards else list(range(steps))
                for out_t, t in enumerate(order):
                    z = seq[bi, t] @ w + h @ u + b
                    i = np.array([sig(v) for v in z[:units]], dtype=np.float32)
                    f = np.array([sig(v) for v in z[units : 2 * units]], dtype=np.float32)
                    g = np.tanh(z[2 * units : 3 * units]).astype(np.float32)
                    o = np.array([sig(v) for v in z[3 * units :]], dtype=np.float32)
                    c = f * c + i * g
                    h = o * np.tanh(c)
                    outs[bi, out_t] = h
            return outs

        with h5py.File(io.BytesIO(hf_bytes), "r") as hf:
            enc_last = ref_lstm_step_seq(past, hf["layers/lstm"], go_backwards=False)[:, -1, :]
            dec_in = np.concatenate([np.repeat(enc_last[:, None, :], 12, axis=1), fut], axis=-1)
            fwd = ref_lstm_step_seq(dec_in, hf["layers/bidirectional/forward_layer"], go_backwards=False)
            bwd_raw = ref_lstm_step_seq(
                dec_in, hf["layers/bidirectional/backward_layer"], go_backwards=True
            )
            merged = np.concatenate([fwd, bwd_raw[:, ::-1, :]], axis=-1)
            d1_w = np.asarray(hf["layers/dense/vars/0"], dtype=np.float32)
            d1_b = np.asarray(hf["layers/dense/vars/1"], dtype=np.float32)
            d2_w = np.asarray(hf["layers/dense_1/vars/0"], dtype=np.float32)
            d2_b = np.asarray(hf["layers/dense_1/vars/1"], dtype=np.float32)
            h1 = np.maximum(merged @ d1_w + d1_b, 0.0)
            ref_out = (h1 @ d2_w + d2_b).reshape(2, 12).astype(np.float32)

        max_diff = float(np.max(np.abs(pred - ref_out)))
        self.assertLess(max_diff, 1e-5)


if __name__ == "__main__":
    unittest.main()
