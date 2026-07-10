# SPDX-License-Identifier: MIT
"""Place-name geocoding via the public Nominatim API (OpenStreetMap).

Usage policy (https://operations.osmfoundation.org/policies/nominatim/),
followed here:
  - A descriptive User-Agent identifying the application is REQUIRED
    (Nominatim blocks requests with generic/default UA strings). A
    published addon should extend NOMINATIM_USER_AGENT with a maintainer
    contact (email or project URL) per the policy's "please provide a
    valid HTTP Referer or User-Agent identifying the application" clause.
  - Max ~1 request/second. This module is only ever called from a single
    user-initiated "Search" click, never in a loop, but still enforces a
    minimum gap between calls defensively (_last_call_at) so rapid
    double-clicks can't burst the service.
  - No bulk/systematic geocoding, no auto-complete-on-keystroke queries,
    results are cached per dialog session only (not persisted to disk).
  - format=json, limit capped at 5 results -- small, human-scale queries.

This module does no FreeCAD-API work and is safe to call from a background
QThread (see add_location_dialog.py), keeping the network round-trip off
the GUI thread.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "FreeCAD-SiteContext-addon/0.2 (+https://github.com/mathmati; contact via GitHub)"
MIN_REQUEST_INTERVAL_S = 1.0

_last_call_at = [0.0]


class GeocodeError(RuntimeError):
    pass


def _respect_rate_limit():
    elapsed = time.time() - _last_call_at[0]
    if elapsed < MIN_REQUEST_INTERVAL_S:
        time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)


def geocode_search(query, limit=5, timeout=15):
    """Return a list of {"display_name", "lat", "lon"} dicts for a free-text
    place-name query, most-relevant first. Raises GeocodeError on failure.
    """
    query = (query or "").strip()
    if not query:
        return []

    _respect_rate_limit()
    params = {"q": query, "format": "json", "limit": str(int(limit))}
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": NOMINATIM_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.URLError as exc:
        raise GeocodeError(f"Nominatim request failed: {exc}") from exc
    finally:
        _last_call_at[0] = time.time()

    try:
        results = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise GeocodeError(f"Nominatim returned unparseable data: {exc}") from exc

    out = []
    for item in results:
        try:
            out.append(
                {
                    "display_name": item["display_name"],
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out
