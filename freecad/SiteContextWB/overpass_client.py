# SPDX-License-Identifier: MIT
"""Overpass API client: fetch OSM building footprints for a bbox.

v0.2 change from the prototype: queries BOTH `way["building"]` AND
`rel["building"]` (multipolygon relations) in one union query with
`out geom`, so inline node/way geometry comes back for both -- v0 only
queried ways and silently skipped every relation-tagged building
(courtyards, complex campuses).

This module does no FreeCAD-API work and is safe to call from a
background QThread -- see add_location_dialog.py.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OVERPASS_USER_AGENT = "FreeCAD-SiteContext-addon/0.2 (oss-unlock mission 3 v0.2)"


class OverpassError(RuntimeError):
    pass


def build_query(s, w, n, e, timeout_s=25):
    return (
        f"[out:json][timeout:{timeout_s}];"
        f'(way["building"]({s},{w},{n},{e});'
        f'rel["building"]({s},{w},{n},{e}););'
        f"out geom;"
    )


def fetch_overpass_bbox(s, w, n, e, cache_path=None, progress_cb=None):
    """Fetch building ways+relations for a bbox. If cache_path exists,
    returns its content with zero network calls (politeness: a given
    bbox is only ever fetched once). Tries overpass-api.de first, falls
    back to the kumi.systems mirror on error.
    """
    if cache_path and _cache_exists(cache_path):
        if progress_cb:
            progress_cb(f"using cached Overpass response: {cache_path}")
        return _read_cache(cache_path)

    query = build_query(s, w, n, e)
    data_bytes = urllib.parse.urlencode({"data": query}).encode("utf-8")

    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            if progress_cb:
                progress_cb(f"fetching OSM buildings from {endpoint} ...")
            req = urllib.request.Request(
                endpoint, data=data_bytes, headers={"User-Agent": OVERPASS_USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw = resp.read()
            result = json.loads(raw.decode("utf-8"))
            if cache_path:
                _write_cache(cache_path, raw)
            return result
        except Exception as exc:  # noqa: BLE001 - try next mirror
            last_err = exc
            time.sleep(1.0)
    raise OverpassError(f"All Overpass endpoints failed: {last_err}")


def _cache_exists(path):
    import os

    return os.path.exists(path)


def _read_cache(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_cache(path, raw_bytes):
    import os

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(raw_bytes)
