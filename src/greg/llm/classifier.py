"""LLM-assisted transaction signature classifier with strict category validation."""
from __future__ import annotations

import json

from greg.config import load_settings
from greg.domain.categories import VALID_CATEGORIES
from greg.llm.client import call_llm


def classify_signatures_with_llm(signatures: list[dict[str, str]]) -> dict[str, str]:
    """Use the configured LLM to classify unique CSV signatures into Salaries, Taxes, or Other."""
    if not load_settings().llm_base_url or not signatures:
        return {}

    capped = signatures[:60]
    lines = [
        (
            f'{idx}: counterparty="{s["counterparty"]}" | type="{s["raw_type"]}" | '
            f'direction="{s["direction"]}" | ref="{s["reference"][:80]}"'
        )
        for idx, s in enumerate(capped)
    ]
    prompt = (
        "Classify each bank transaction signature into exactly one category: "
        '"Salaries", "Taxes", or "Other".\n'
        "- Salaries: employee or owner wages, payroll, salary payments.\n"
        "- Taxes: direct payments to tax authorities (income tax, wage tax, trade tax, VAT). "
        "Note: health insurance or social security contributions are Other, not Taxes.\n"
        "- Other: everything else (sales, rent, suppliers, utilities, insurance, transfers).\n\n"
        "Respond ONLY with a valid JSON object mapping the numeric index string to the category, "
        'e.g. {"0": "Other", "1": "Salaries"}.\n\n'
        + "\n".join(lines)
    )
    try:
        res = call_llm(
            [
                {"role": "system", "content": "You are a strict JSON transaction classifier."},
                {"role": "user", "content": prompt},
            ]
        )
        raw = res.get("reply", "").strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        parsed = json.loads(raw[start : end + 1])
        if not isinstance(parsed, dict):
            return {}
        mapping: dict[str, str] = {}
        for k, v in parsed.items():
            try:
                idx = int(k)
            except (ValueError, TypeError):
                continue
            if 0 <= idx < len(capped) and v in VALID_CATEGORIES:
                mapping[capped[idx]["key"]] = v
        return mapping
    except Exception:
        return {}
