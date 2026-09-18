"""Pricing and optional WooCommerce checkout views."""

from __future__ import annotations

import requests
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import debt_pricing, payments
from .forms import CheckoutForm
from .models import Payment


def pricing(request):
    price = debt_pricing.current_debt_price()
    return render(
        request,
        "importer/pricing.html",
        {
            "plan_name": settings.EASYIMPORTS_PLAN_NAME,
            "price_label": price.label,
            "price_record_date": price.record_date,
            "price_is_live": price.is_live,
            "payment_available": payments.payment_available(),
            "woocommerce_configured": payments.woocommerce_configured(),
            "form": CheckoutForm(),
        },
    )


@require_POST
def checkout(request):
    form = CheckoutForm(request.POST)
    email = form.cleaned_data.get("email", "") if form.is_valid() else ""
    price = debt_pricing.current_debt_price()
    payment = Payment.objects.create(
        email=email,
        plan_name=settings.EASYIMPORTS_PLAN_NAME,
        amount_label=price.label,
    )
    if payments.woocommerce_configured():
        try:
            order = payments.create_woocommerce_order(email=email or None)
        except (
            requests.RequestException,
            payments.PaymentConfigError,
            ValueError,
        ) as exc:
            payment.status = Payment.Status.FAILED
            payment.error_message = f"{type(exc).__name__}: {exc}"
            payment.save(update_fields=["status", "error_message", "updated_at"])
            messages.error(
                request, "Could not start WooCommerce checkout. Please try again later."
            )
            return redirect("importer:pricing")
        payment.wc_order_id = order.get("id")
        payment.wc_order_key = order.get("order_key", "") or ""
        payment.payment_url = payments.order_payment_url(order)
        payment.status = Payment.Status.PENDING
        payment.save()
        return redirect(payment.payment_url)
    fallback = payments.checkout_fallback_url()
    if fallback:
        payment.status = Payment.Status.PENDING
        payment.payment_url = fallback
        payment.save(update_fields=["status", "payment_url", "updated_at"])
        return redirect(fallback)
    payment.status = Payment.Status.FAILED
    payment.error_message = "Payments are not configured."
    payment.save(update_fields=["status", "error_message", "updated_at"])
    messages.error(request, "Payments are not configured on this server yet.")
    return redirect("importer:pricing")


_WC_STATUS_MAP = {
    "completed": Payment.Status.COMPLETED,
    "processing": Payment.Status.COMPLETED,
    "pending": Payment.Status.PENDING,
    "on-hold": Payment.Status.PENDING,
    "failed": Payment.Status.FAILED,
    "cancelled": Payment.Status.FAILED,
    "refunded": Payment.Status.FAILED,
}


def payment_return(request):
    order_id = request.GET.get("order_id") or request.GET.get("order")
    payment = None
    if order_id:
        try:
            payment = Payment.objects.filter(wc_order_id=int(order_id)).first()
        except (TypeError, ValueError):
            pass
    if payment and payment.wc_order_id and payments.woocommerce_configured():
        status = payments.fetch_order_status(payment.wc_order_id)
        mapped = _WC_STATUS_MAP.get(status or "")
        if mapped:
            payment.status = mapped
            payment.save(update_fields=["status", "updated_at"])
    return render(
        request,
        "importer/payment_return.html",
        {
            "payment": payment,
            "plan_name": settings.EASYIMPORTS_PLAN_NAME,
        },
    )
