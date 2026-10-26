"""Extract only continuation values from provider Link headers."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit


def next_link_value(link: str | None, key: str) -> str | None:
    """Read a next-page query parameter; never follow the URL in the header."""
    for entry in (link or "").split(","):
        if 'rel="next"' not in entry and "rel=next" not in entry:
            continue
        if 'results="false"' in entry:
            continue
        url = entry.split(">", 1)[0].lstrip(" <")
        values = parse_qs(urlsplit(url).query).get(key)
        if values:
            return values[0]
    return None
