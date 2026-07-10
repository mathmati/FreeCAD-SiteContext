# SPDX-License-Identifier: MIT
"""Lat/lon <-> local-meters projection helpers.

Ported from the v0 prototype (sitecontext_proto.py). Deliberately a simple
equirectangular (plate-carree) approximation, NOT a conformal or
equal-area projection -- see project_latlon()'s docstring for the error
budget. A future version should replace this with a proper local
projection (UTM zone via pyproj, or FreeCAD's own georeferencing/
Coordinates tooling) so footprints stay metrically correct as the area of
interest grows and so exports interoperate with GIS tools.
"""
import math

M_PER_DEG_LAT = 111_320.0


def project_latlon(lat, lon, lat0, lon0):
    """Equirectangular approximation around origin (lat0, lon0).

    Treats degrees of longitude as having a constant meters-per-degree
    scale factor (cos(lat0)) valid at the origin latitude, and degrees of
    latitude as a flat constant (111,320 m/deg, WGS84 mean). Meters are
    exact AT the origin; error grows with distance from the origin and
    with absolute latitude. Negligible (well under 1cm) over a few hundred
    meters at temperate latitudes -- the addon's supported radius range
    (100-500m) -- NOT acceptable at kilometer scale or near the poles.
    """
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    x = (lon - lon0) * m_per_deg_lon
    y = (lat - lat0) * M_PER_DEG_LAT
    return x, y


def bbox_from_center_radius(lat, lon, radius_m):
    """Return a square (S, W, N, E) bbox of half-width radius_m around a
    center point. A circle-of-interest expressed as its bounding square --
    a deliberate simplification (documented in README) matching the
    dialog's "radius" field; Overpass/opentopodata both operate on boxes.
    """
    lat_delta = radius_m / M_PER_DEG_LAT
    lon_delta = radius_m / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return (lat - lat_delta, lon - lon_delta, lat + lat_delta, lon + lon_delta)
