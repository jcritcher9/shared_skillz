from __future__ import annotations

"""Product OAuth redirect URI resolve + validation (HTTPS-1).

Binding freeze: ``crm_oauth_https_redirect_uri.md``. Identical section A/B
rules are enforced authoritatively in mappings_2 registration store; Django
must not import mappings_2 at runtime, so this module duplicates pure rules.
"""

import ipaddress
import os
import re
import socket
from typing import Literal
from urllib.parse import urlparse

OAuthProvider = Literal["salesforce", "hubspot"]

OAUTH_CALLBACK_PATH = "/crm/oauth/callback/"
LEGACY_HTTP_OAUTH_CALLBACK_URI = "http://127.0.0.1:8001/crm/oauth/callback/"
HTTPS_BUILTIN_OAUTH_CALLBACK_URI = "https://127.0.0.1:8001/crm/oauth/callback/"
# HTTPS-3: product built-in is HTTPS loopback (section A legacy exemption removed).
OAUTH_HTTPS_BUILTIN_DEFAULT = True
OAUTH_REDIRECT_MAX_LEN = 512

ENV_UNIFIED_OAUTH_REDIRECT_URI = "EASYIMPORTS_OAUTH_REDIRECT_URI"
ENV_LEGACY_HUBSPOT_REDIRECT_URI = "EASYIMPORTS_HUBSPOT_REDIRECT_URI"

_DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_PROVIDERS = frozenset({"salesforce", "hubspot"})


class InvalidOAuthRedirectUri(ValueError):
    """OAuth redirect URI failed closed validation (operator-safe message)."""


def validate_oauth_redirect_uri(
    value: str,
    *,
    allow_legacy_http_builtin: bool = False,
) -> str:
    """Return stripped URI if valid; raise :class:`InvalidOAuthRedirectUri`."""

    text = str(value or "").strip()
    if not text:
        raise InvalidOAuthRedirectUri("OAuth redirect URI is required.")
    if len(text) > OAUTH_REDIRECT_MAX_LEN:
        raise InvalidOAuthRedirectUri("OAuth redirect URI exceeds maximum length.")
    if allow_legacy_http_builtin and text == LEGACY_HTTP_OAUTH_CALLBACK_URI:
        return text
    return _validate_section_b(text)


def resolve_oauth_redirect_uri(*, provider: OAuthProvider | str) -> str:
    """Effective product callback for the given CRM provider (required).

    Zero-argument resolve is forbidden — HubSpot-only legacy env must not
    apply to Salesforce.
    """

    key = str(provider or "").strip().lower()
    if key not in _PROVIDERS:
        raise InvalidOAuthRedirectUri(
            f"Unsupported OAuth redirect provider {key!r}."
        )

    unified = _env_nonempty(ENV_UNIFIED_OAUTH_REDIRECT_URI)
    legacy_hs = _env_nonempty(ENV_LEGACY_HUBSPOT_REDIRECT_URI)

    if key == "salesforce":
        if unified is not None:
            return validate_oauth_redirect_uri(
                unified, allow_legacy_http_builtin=False
            )
        return _builtin_redirect_uri()

    # hubspot
    if unified is not None:
        return validate_oauth_redirect_uri(
            unified, allow_legacy_http_builtin=False
        )
    if legacy_hs is not None:
        return validate_oauth_redirect_uri(
            legacy_hs, allow_legacy_http_builtin=False
        )
    return _builtin_redirect_uri()


def resolve_salesforce_oauth_redirect_uri() -> str:
    return resolve_oauth_redirect_uri(provider="salesforce")


def resolve_hubspot_oauth_redirect_uri() -> str:
    return resolve_oauth_redirect_uri(provider="hubspot")


def _builtin_redirect_uri() -> str:
    if not OAUTH_HTTPS_BUILTIN_DEFAULT:
        return LEGACY_HTTP_OAUTH_CALLBACK_URI
    return validate_oauth_redirect_uri(
        HTTPS_BUILTIN_OAUTH_CALLBACK_URI,
        allow_legacy_http_builtin=False,
    )


def _env_nonempty(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    text = str(raw).strip()
    return text if text else None


def _validate_section_b(text: str) -> str:
    try:
        parsed = urlparse(text)
    except ValueError as exc:
        raise InvalidOAuthRedirectUri("OAuth redirect URI is not a valid URL.") from exc

    if (parsed.scheme or "").lower() != "https":
        raise InvalidOAuthRedirectUri("OAuth redirect URI must use https.")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidOAuthRedirectUri(
            "OAuth redirect URI must not include credentials."
        )
    if parsed.query or parsed.fragment:
        raise InvalidOAuthRedirectUri(
            "OAuth redirect URI must not include query or fragment."
        )
    # Fail closed on matrix/path params (";x") even when path string looks exact.
    if parsed.params or parsed.path != OAUTH_CALLBACK_PATH or ";" in parsed.path:
        raise InvalidOAuthRedirectUri(
            "OAuth redirect URI path must be /crm/oauth/callback/."
        )

    host, port = _authority_host_port(parsed.netloc)
    if not host:
        raise InvalidOAuthRedirectUri("OAuth redirect URI host is required.")
    if port is not None and not (1 <= port <= 65535):
        raise InvalidOAuthRedirectUri("OAuth redirect URI port is out of range.")

    if not _host_allowed(host):
        raise InvalidOAuthRedirectUri("OAuth redirect URI host is not allowed.")

    return text


def _authority_host_port(netloc: str) -> tuple[str, int | None]:
    """Parse host and optional port; reject empty/non-numeric port syntax."""

    authority = str(netloc or "")
    if not authority:
        raise InvalidOAuthRedirectUri("OAuth redirect URI host is required.")
    if "@" in authority:
        raise InvalidOAuthRedirectUri(
            "OAuth redirect URI must not include credentials."
        )

    if authority.startswith("["):
        close = authority.find("]")
        if close <= 1:
            raise InvalidOAuthRedirectUri("OAuth redirect URI host is not allowed.")
        host = authority[1:close]
        rest = authority[close + 1 :]
        if rest == "":
            return host, None
        if not rest.startswith(":") or rest == ":":
            raise InvalidOAuthRedirectUri(
                "OAuth redirect URI port must be numeric."
            )
        port_text = rest[1:]
        if not port_text.isdigit():
            raise InvalidOAuthRedirectUri(
                "OAuth redirect URI port must be numeric."
            )
        return host, int(port_text)

    if authority.endswith(":") or authority.count(":") > 1:
        raise InvalidOAuthRedirectUri("OAuth redirect URI port must be numeric.")
    if ":" in authority:
        host, _, port_text = authority.partition(":")
        if not host or not port_text.isdigit():
            raise InvalidOAuthRedirectUri(
                "OAuth redirect URI port must be numeric."
            )
        return host, int(port_text)
    return authority, None


def _host_allowed(host: str) -> bool:
    # Exact product loopback string only (not 127.1 / 0x7f.0.0.1 / etc.).
    if host == "127.0.0.1" or host.lower() == "localhost":
        return True
    if (
        _is_ip_literal(host)
        or _is_inet_aton_host(host)
        or _looks_like_ipv4_shaped(host)
    ):
        return False
    return _is_dns_hostname(host)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_inet_aton_host(host: str) -> bool:
    """True when platform inet_aton accepts host (incl. 127.1, 0x7f.0.0.1, etc.)."""

    try:
        socket.inet_aton(host)
        return True
    except OSError:
        return False


def _looks_like_ipv4_shaped(host: str) -> bool:
    """Dotted labels that are only numeric/hex/octal-like IPv4 fragments.

    Covers abbreviated forms (``127.1``, ``127.0.1``) and non-decimal octets
    even if a platform's inet_aton is strict.
    """

    parts = host.split(".")
    if not (1 <= len(parts) <= 4):
        return False
    return all(_is_ipv4_octet_token(part) for part in parts)


def _is_ipv4_octet_token(part: str) -> bool:
    if not part:
        return False
    lower = part.lower()
    if lower.startswith("0x"):
        if len(lower) == 2:
            return False
        try:
            int(lower, 16)
            return True
        except ValueError:
            return False
    return part.isdigit()


def _is_dns_hostname(host: str) -> bool:
    if not host or len(host) > 253 or host.endswith("."):
        return False
    if "." not in host:
        return False
    labels = host.split(".")
    if any(not label for label in labels):
        return False
    return all(_DNS_LABEL_RE.fullmatch(label) for label in labels)
