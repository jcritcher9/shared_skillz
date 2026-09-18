"""Live price label backed by the U.S. Treasury Debt to the Penny API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

DEBT_TO_PENNY_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    "v2/accounting/od/debt_to_penny"
)
CACHE_KEY = "easyimports:debt-price:v1"


@dataclass(frozen=True)
class DebtPrice:
    label: str
    record_date: str
    is_live: bool


def current_debt_price() -> DebtPrice:
    """Return the latest national-debt price label, falling back safely."""
    cached = cache.get(CACHE_KEY)
    if isinstance(cached, DebtPrice):
        return cached

    try:
        price = _fetch_debt_price()
    except (LookupError, ValueError, requests.RequestException):
        return _fallback_debt_price()

    cache.set(
        CACHE_KEY,
        price,
        timeout=settings.EASYIMPORTS_DEBT_PRICE_CACHE_SECONDS,
    )
    return price


def _fetch_debt_price() -> DebtPrice:
    response = requests.get(
        DEBT_TO_PENNY_URL,
        params={
            "fields": "record_date,tot_pub_debt_out_amt",
            "sort": "-record_date",
            "page[size]": "1",
        },
        timeout=settings.EASYIMPORTS_DEBT_PRICE_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not rows:
        raise LookupError("Treasury response did not include debt records.")

    row: Any = rows[0]
    if not isinstance(row, dict):
        raise LookupError("Treasury response included an invalid debt record.")

    raw_amount = row.get("tot_pub_debt_out_amt")
    raw_record_date = row.get("record_date")
    if not raw_amount or not raw_record_date:
        raise LookupError("Treasury response omitted debt amount or record date.")

    try:
        parsed_date = date.fromisoformat(str(raw_record_date))
    except ValueError as exc:
        raise ValueError("Treasury record date was not ISO formatted.") from exc

    return DebtPrice(
        label=_format_currency(raw_amount),
        record_date=parsed_date.isoformat(),
        is_live=True,
    )


def _fallback_debt_price() -> DebtPrice:
    return DebtPrice(
        label=settings.EASYIMPORTS_DEBT_PRICE_FALLBACK_LABEL,
        record_date=settings.EASYIMPORTS_DEBT_PRICE_FALLBACK_DATE,
        is_live=False,
    )


def _format_currency(raw_amount: Any) -> str:
    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Treasury debt amount was not numeric.") from exc
    return f"${amount:,.2f}"
