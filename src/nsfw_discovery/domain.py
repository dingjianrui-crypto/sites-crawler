from __future__ import annotations

from urllib.parse import urlparse


COMMON_SECOND_LEVELS = {
    "co",
    "com",
    "net",
    "org",
    "gov",
    "ac",
    "edu",
}


def normalize_domain(url_or_domain: str) -> str:
    value = url_or_domain.strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip(".")


def registrable_domain(url_or_domain: str) -> str:
    host = normalize_domain(url_or_domain)
    parts = [part for part in host.split(".") if part]
    if len(parts) <= 2:
        return host
    if len(parts[-1]) == 2 and parts[-2] in COMMON_SECOND_LEVELS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def same_domain(url: str, domain: str) -> bool:
    host = normalize_domain(url)
    root = registrable_domain(domain)
    return host == root or host.endswith(f".{root}")
