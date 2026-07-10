# SPDX-License-Identifier: MIT
"""Terrain v1: coarse elevation sampling via the open api.opentopodata.org
SRTM90m dataset, and a heightfield mesh builder.

Politeness (https://www.opentopodata.org/#public-api): the public instance
asks for <=100 locations per request and roughly 1 request/second; this
module batches the sample grid into chunks of <=100 and sleeps between
requests. No API key, no authentication -- it is explicitly a free public
service the docs ask callers to be gentle with.

This module does no FreeCAD-API work for the *sampling* half (safe on a
background QThread); build_terrain_mesh() below DOES use the Mesh module
and must be called from the main thread like the rest of the geometry
pipeline.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .projection import project_latlon

OPENTOPODATA_URL = "https://api.opentopodata.org/v1/srtm90m"
OPENTOPODATA_USER_AGENT = "FreeCAD-SiteContext-addon/0.2 (oss-unlock mission 3 v0.2)"
BATCH_SIZE = 100
MIN_REQUEST_INTERVAL_S = 1.0
RELIEF_THRESHOLD_M = 2.0
GRID_N = 15  # 15x15 sample grid, coarse-but-cheap per the task brief


class TerrainError(RuntimeError):
    pass


def sample_grid_points(s, w, n, e, grid_n=GRID_N):
    """Return a grid_n x grid_n list-of-lists of (lat, lon) sample points
    evenly spaced across the bbox (inclusive of the edges)."""
    rows = []
    for i in range(grid_n):
        lat = s + (n - s) * i / (grid_n - 1)
        row = []
        for j in range(grid_n):
            lon = w + (e - w) * j / (grid_n - 1)
            row.append((lat, lon))
        rows.append(row)
    return rows


def fetch_elevation_grid(s, w, n, e, grid_n=GRID_N, progress_cb=None):
    """Sample elevation on a grid_n x grid_n grid over the bbox. Returns a
    grid_n x grid_n list-of-lists of elevation floats (meters), or raises
    TerrainError if the API is unreachable/erroring -- callers should catch
    this and fall back to a flat ground plane gracefully, per the task
    brief ("if flat or API unavailable, fall back...").
    """
    points = sample_grid_points(s, w, n, e, grid_n)
    flat_points = [pt for row in points for pt in row]

    elevations = {}
    last_call = 0.0
    for start in range(0, len(flat_points), BATCH_SIZE):
        chunk = flat_points[start : start + BATCH_SIZE]
        elapsed = time.time() - last_call
        if last_call and elapsed < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)

        locs = "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in chunk)
        url = OPENTOPODATA_URL + "?" + urllib.parse.urlencode({"locations": locs})
        req = urllib.request.Request(url, headers={"User-Agent": OPENTOPODATA_USER_AGENT})
        if progress_cb:
            progress_cb(
                f"sampling terrain elevation {start + len(chunk)}/{len(flat_points)} ..."
            )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.URLError as exc:
            raise TerrainError(f"opentopodata request failed: {exc}") from exc
        finally:
            last_call = time.time()

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TerrainError(f"opentopodata returned unparseable data: {exc}") from exc
        if payload.get("status") != "OK":
            raise TerrainError(f"opentopodata status: {payload.get('status')}")

        for (lat, lon), result in zip(chunk, payload.get("results", [])):
            elev = result.get("elevation")
            if elev is None:
                raise TerrainError("opentopodata returned a null elevation")
            elevations[(round(lat, 6), round(lon, 6))] = float(elev)

    grid = []
    for row in points:
        grid.append([elevations[(round(lat, 6), round(lon, 6))] for lat, lon in row])
    return grid


def relief(elevation_grid):
    flat = [v for row in elevation_grid for v in row]
    return max(flat) - min(flat)


def build_terrain_mesh(Mesh, FreeCAD, elevation_grid, sample_points, lat0, lon0, datum_elev):
    """Build a Mesh.Mesh heightfield surface from the sampled grid.

    elevation_grid / sample_points: grid_n x grid_n parallel arrays (meters
    elevation; (lat,lon) per sample_points). datum_elev is subtracted from
    every sample so the mesh sits with its local mean around z=0 in the
    document (matching the flat-ground-plane fallback's frame); buildings
    themselves stay flat-based at their own local footprint (documented
    simplification -- see README "Accuracy limits").
    """
    grid_n = len(elevation_grid)
    xy = [
        [project_latlon(lat, lon, lat0, lon0) for lat, lon in row]
        for row in sample_points
    ]

    def vertex(i, j):
        x, y = xy[i][j]
        z = elevation_grid[i][j] - datum_elev
        return FreeCAD.Vector(x * 1000.0, y * 1000.0, z * 1000.0)

    facets = []
    for i in range(grid_n - 1):
        for j in range(grid_n - 1):
            v00 = vertex(i, j)
            v01 = vertex(i, j + 1)
            v10 = vertex(i + 1, j)
            v11 = vertex(i + 1, j + 1)
            facets.append((v00, v10, v11))
            facets.append((v00, v11, v01))

    mesh = Mesh.Mesh(facets)
    return mesh
