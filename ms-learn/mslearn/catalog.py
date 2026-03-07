"""MS Learn Catalog API client — discovers learning paths, modules, and units."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import requests

from .models import LearningPath, Module, Unit

log = logging.getLogger(__name__)

CATALOG_API = "https://learn.microsoft.com/api/catalog/"


def parse_input_url(url: str) -> tuple[str, str, str]:
    """Parse a MS Learn URL into (content_type, slug, locale).

    Returns:
        ("path", slug, locale) for learning path URLs
        ("module", slug, locale) for module URLs
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    # Extract locale (e.g. /en-us/)
    locale_match = re.search(r"/([a-z]{2}-[a-z]{2})/", path)
    locale = locale_match.group(1) if locale_match else "en-us"

    if "/training/paths/" in path:
        slug = path.split("/training/paths/")[-1].strip("/")
        return ("path", slug, locale)
    elif "/training/modules/" in path:
        # Could be module or unit URL — take just the module slug
        after = path.split("/training/modules/")[-1].strip("/")
        slug = after.split("/")[0]  # drop unit part if present
        return ("module", slug, locale)
    else:
        raise ValueError(
            f"Unsupported URL format: {url}\n"
            "Expected /training/paths/... or /training/modules/..."
        )


def _find_uid_by_slug(items: list[dict], slug: str) -> dict | None:
    """Find a catalog item whose URL contains the given slug."""
    for item in items:
        item_url = item.get("url", "")
        # Strip query params and trailing slash before comparing
        path = urlparse(item_url).path.rstrip("/")
        if path.endswith(slug):
            return item
    return None


def fetch_learning_path(slug: str, locale: str = "en-us") -> LearningPath:
    """Fetch learning path metadata from the Catalog API."""
    resp = requests.get(
        CATALOG_API,
        params={"type": "learningPaths,modules,units", "locale": locale},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # Find the learning path by slug
    lp_data = _find_uid_by_slug(data.get("learningPaths", []), slug)
    if not lp_data:
        raise ValueError(f"Learning path not found for slug: {slug}")

    # Build unit lookup
    unit_map = {u["uid"]: u for u in data.get("units", [])}

    # Build module lookup
    module_map = {m["uid"]: m for m in data.get("modules", [])}

    modules = []
    for mod_uid in lp_data.get("modules", []):
        mod_data = module_map.get(mod_uid)
        if not mod_data:
            log.warning("Module %s not found in catalog", mod_uid)
            continue

        units = []
        for unit_uid in mod_data.get("units", []):
            u = unit_map.get(unit_uid, {})
            units.append(Unit(
                uid=unit_uid,
                title=u.get("title", unit_uid.split(".")[-1]),
                url="",  # populated by scraper from module page TOC
                duration_minutes=u.get("duration_in_minutes", 0),
            ))

        # Clean URL (remove query params)
        mod_url = mod_data.get("url", "").split("?")[0].rstrip("/")
        modules.append(Module(
            uid=mod_uid,
            title=mod_data.get("title", ""),
            url=mod_url,
            duration_minutes=mod_data.get("duration_in_minutes", 0),
            summary=mod_data.get("summary", ""),
            units=units,
        ))

    return LearningPath(
        uid=lp_data.get("uid", ""),
        title=lp_data.get("title", ""),
        url=lp_data.get("url", ""),
        duration_minutes=lp_data.get("duration_in_minutes", 0),
        summary=lp_data.get("summary", ""),
        modules=modules,
    )


def fetch_module(slug: str, locale: str = "en-us") -> Module:
    """Fetch a single module's metadata from the Catalog API."""
    resp = requests.get(
        CATALOG_API,
        params={"type": "modules,units", "locale": locale},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    mod_data = _find_uid_by_slug(data.get("modules", []), slug)
    if not mod_data:
        raise ValueError(f"Module not found for slug: {slug}")

    unit_map = {u["uid"]: u for u in data.get("units", [])}

    units = []
    for unit_uid in mod_data.get("units", []):
        u = unit_map.get(unit_uid, {})
        units.append(Unit(
            uid=unit_uid,
            title=u.get("title", unit_uid.split(".")[-1]),
            url="",
            duration_minutes=u.get("duration_in_minutes", 0),
        ))

    mod_url = mod_data.get("url", "").split("?")[0].rstrip("/")
    return Module(
        uid=mod_data.get("uid", ""),
        title=mod_data.get("title", ""),
        url=mod_url,
        duration_minutes=mod_data.get("duration_in_minutes", 0),
        summary=mod_data.get("summary", ""),
        units=units,
    )
