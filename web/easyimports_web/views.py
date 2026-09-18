"""Closed operator-facing error handlers. No exception or URLconf detail."""

from __future__ import annotations

from django.shortcuts import render


def closed_404(request, exception=None):
    return render(
        request,
        "importer/closed_error.html",
        {
            "heading": "This page isn't available",
            "lead": (
                "That step is no longer available. Return to EasyImports to "
                "continue."
            ),
        },
        status=404,
    )


def closed_500(request):
    return render(
        request,
        "importer/closed_500.html",
        {
            "heading": "Something went wrong",
            "lead": (
                "EasyImports could not complete that request. No additional "
                "details are shown here."
            ),
        },
        status=500,
    )
