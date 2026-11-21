"""Transaction classification rules and strict category normalization."""
from __future__ import annotations

import re

VALID_CATEGORIES = ("Salaries", "Taxes", "Other")

SOCIAL_SECURITY_KEYWORDS = (
    "krankenkasse",
    "social security",
    "social health",
    "health insurance",
    "pension insurance",
    "sozialversicherung",
)

_TAX_PHRASES = (
    "tax office",
    "finanzamt",
    "wage tax",
    "income tax",
    "trade tax",
    "corporate tax",
    "sales tax",
    "lohnsteuer",
    "einkommensteuer",
    "gewerbesteuer",
    "umsatzsteuer",
    "körperschaftsteuer",
    "solidarity surcharge",
    "solidaritätszuschlag",
    "tax prepayment",
    "tax transfer",
    "tax payment",
    "urząd skarbowy",
    "hmrc",
    "irs ",
)

_TAX_WORD_RE = re.compile(r"\b(tax|taxes|vat|mwst|ust|steuer|steuern)\b")

_SALARY_PHRASES = (
    "salary",
    "salaries",
    "payroll",
    "gehalt",
    "gehälter",
    "lohn",
    "wages",
    "net pay",
    "staff pay",
)


def is_social_security(raw_type: str, counterparty: str, reference: str) -> bool:
    """Return True if the transaction metadata refers to social/health insurance."""
    text = f"{raw_type} {counterparty} {reference}".lower()
    return any(kw in text for kw in SOCIAL_SECURITY_KEYWORDS)


def classify_category_keywords(raw_type: str, counterparty: str, reference: str) -> str:
    """Deterministically classify a transaction into Salaries, Taxes, or Other."""
    text = f"{raw_type} {counterparty} {reference}".lower()
    raw_lower = raw_type.lower().strip()

    # Social security / health insurance funds belong under Other, not Taxes.
    if is_social_security(raw_type, counterparty, reference):
        return "Other"

    if (
        raw_lower in ("tax", "taxes", "tax office transfer")
        or any(phrase in text for phrase in _TAX_PHRASES)
        or _TAX_WORD_RE.search(text)
    ):
        return "Taxes"

    if raw_lower in ("salary", "salaries", "salary payment", "payroll") or any(
        phrase in text for phrase in _SALARY_PHRASES
    ):
        return "Salaries"

    return "Other"


def normalize_category(
    cat: str, raw_type: str = "", counterparty: str = "", reference: str = ""
) -> str:
    """Normalize a category string strictly to Salaries, Taxes, or Other."""
    c = (cat or "").strip().lower()
    if c in ("salaries", "salary"):
        return "Salaries"
    if c in ("taxes", "tax"):
        return "Taxes"
    if c == "other":
        return "Other"
    return classify_category_keywords(raw_type or cat, counterparty, reference)
