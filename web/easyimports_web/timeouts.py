"""Parse Django EasyImports API client timeout settings fail-closed."""

from __future__ import annotations

import math


def parse_positive_timeout(
    name: str,
    raw: str | None,
    *,
    default: str,
) -> float:
    """Return a finite timeout seconds value strictly greater than zero.

    Invalid, zero, negative, infinite, and non-numeric overrides fail closed so
    urllib3 never receives an unusable timeout that can surface as an uncaught
    ValueError after a mutation lease is already held (PF2-002).
    """

    material = default if raw is None else str(raw)
    try:
        value = float(material)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be a finite number greater than 0, got {material!r}."
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"{name} must be a finite number greater than 0, got {material!r}."
        )
    return value
