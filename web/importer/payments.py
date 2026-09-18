"""WooCommerce payment integration.

A non-WordPress app offers a "Pay with WooCommerce" option by creating an order
in a WooCommerce store through the WooCommerce REST API and then redirecting the
customer to that order's hosted payment page. When REST credentials are not
configured, we fall back to a static checkout/product URL if one is provided.

Docs: https://woocommerce.github.io/woocommerce-rest-api-docs/#create-an-order
"""

from __future__ import annotations

from typing import Any

import requests
from django.conf import settings


class PaymentConfigError(RuntimeError):
    """Raised when a WooCommerce order is requested but not configured."""


def woocommerce_configured() -> bool:
    """True when enough REST settings exist to create an order via the API."""
    return all(
        [
            settings.WOOCOMMERCE_STORE_URL,
            settings.WOOCOMMERCE_CONSUMER_KEY,
            settings.WOOCOMMERCE_CONSUMER_SECRET,
            settings.WOOCOMMERCE_PRODUCT_ID,
        ]
    )


def checkout_fallback_url() -> str | None:
    """Optional static checkout/product URL used when the REST API is unset."""
    return settings.WOOCOMMERCE_CHECKOUT_URL or None


def payment_available() -> bool:
    return woocommerce_configured() or bool(checkout_fallback_url())


def create_woocommerce_order(*, email: str | None = None) -> dict[str, Any]:
    """Create a pending WooCommerce order and return the parsed JSON response."""
    if not woocommerce_configured():
        raise PaymentConfigError("WooCommerce REST API is not configured.")

    base = settings.WOOCOMMERCE_STORE_URL.rstrip("/")
    url = f"{base}/wp-json/wc/v3/orders"
    payload: dict[str, Any] = {
        "set_paid": False,
        "line_items": [
            {"product_id": int(settings.WOOCOMMERCE_PRODUCT_ID), "quantity": 1}
        ],
    }
    if email:
        payload["billing"] = {"email": email}

    response = requests.post(
        url,
        json=payload,
        auth=(settings.WOOCOMMERCE_CONSUMER_KEY, settings.WOOCOMMERCE_CONSUMER_SECRET),
        timeout=settings.WOOCOMMERCE_HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def order_payment_url(order: dict[str, Any]) -> str:
    """Best-effort hosted payment URL for a WooCommerce order response."""
    url = order.get("payment_url")
    if url:
        return str(url)
    base = settings.WOOCOMMERCE_STORE_URL.rstrip("/")
    order_id = order.get("id")
    order_key = order.get("order_key", "")
    return (
        f"{base}/checkout/order-pay/{order_id}/" f"?pay_for_order=true&key={order_key}"
    )


def fetch_order_status(order_id: int) -> str | None:
    """Fetch a WooCommerce order's status (e.g. 'pending', 'completed')."""
    if not woocommerce_configured():
        return None
    base = settings.WOOCOMMERCE_STORE_URL.rstrip("/")
    url = f"{base}/wp-json/wc/v3/orders/{order_id}"
    try:
        response = requests.get(
            url,
            auth=(
                settings.WOOCOMMERCE_CONSUMER_KEY,
                settings.WOOCOMMERCE_CONSUMER_SECRET,
            ),
            timeout=settings.WOOCOMMERCE_HTTP_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None
    return response.json().get("status")
