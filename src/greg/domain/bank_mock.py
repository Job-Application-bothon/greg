"""Mock connected-bank CSV discovery, labeling, and non-repeating random selection."""
from __future__ import annotations

import random
import re
from pathlib import Path

_TRAILING_NOISE_TOKENS = frozenset(
    {"monthly", "weekly", "daily", "declining", "de", "en", "us", "uk", "format", "mock", "sample"}
)


def list_mock_bank_files(mock_dir: Path) -> list[Path]:
    """Return sorted CSV files available in the configured mock bank directory."""
    if not mock_dir.is_dir():
        return []
    return sorted(
        (p for p in mock_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv"),
        key=lambda p: p.name.lower(),
    )


def format_bank_account_label(filename: str) -> str:
    """Derive a clean human-readable bank account name from a mock CSV filename."""
    stem = Path(filename).stem
    stem = re.sub(r"^\d+[_\-\s]+", "", stem)
    raw_tokens = [tok for tok in re.split(r"[_\-\s]+", stem) if tok]
    trimmed = list(raw_tokens)
    while len(trimmed) > 1 and trimmed[-1].lower() in _TRAILING_NOISE_TOKENS:
        trimmed.pop()
    words = trimmed or raw_tokens or ["Business Account"]
    return " ".join(w.capitalize() if not w.isupper() else w for w in words)


def pick_random_mock_bank_file(
    mock_dir: Path,
    exclude_filename: str | None = None,
    rng: random.Random | None = None,
) -> Path:
    """Pick a random CSV from `mock_dir`, avoiding immediate repeats when multiple exist."""
    files = list_mock_bank_files(mock_dir)
    if not files:
        raise FileNotFoundError(f"No mock bank CSV statements found in {mock_dir}")
    candidates = [f for f in files if f.name != exclude_filename] if exclude_filename else files
    pool = candidates or files
    chooser = rng or random
    return chooser.choice(pool)
