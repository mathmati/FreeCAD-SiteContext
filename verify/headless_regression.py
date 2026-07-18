# SPDX-License-Identifier: MIT
"""verify/headless_regression.py -- SiteContext headless regression (freecadcmd).

Run from the repo root with the wrapper in the README (or plainly):

    freecadcmd verify/headless_regression.py

Exit code 0 and a final "16/16 checks pass" line when green. NO network
access: every fetch is either pure tile math, local synthetic images, or a
mocked client. The live network path is covered separately by
verify/live_imagery_smoke.py (manual).

The 16 checks, in order:

   tile math       1-5    slippy-map tile coords, round-trip, bbox coverage,
                          zoom cap, meters-per-pixel
   stitch/plane    6-9    mosaic + crop pixel geometry, synthetic 2x2 stitch
                          via PIL, ImagePlane metrics, scale cross-check
   dialog choices  10-12  parameter persistence round-trip, fetch-plan
                          matrix (buildings skip), attribution strings
   build pipeline  13-15  legacy 3D build unchanged; 2D map-only build with
                          mocked Overpass (not called, no buildings); 2D map
                          + buildings (mock called, buildings on the map)
   dialog module   16     add_location_dialog imports headless
"""
import math
import os
import sys
import tempfile
import traceback

# --- make the workbench importable from THIS source checkout ----------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
import freecad  # FreeCAD's own namespace package (present under freecadcmd)

freecad.__path__ = [os.path.join(_REPO_ROOT, "freecad")] + list(freecad.__path__)
# freecadcmd auto-imports Mod/ addons at startup; an installed SiteContextWB
# would shadow this checkout, so purge it before importing ours.
for _mod in [
    m
    for m in list(sys.modules)
    if m == "freecad.SiteContextWB" or m.startswith("freecad.SiteContextWB.")
]:
    del sys.modules[_mod]

import FreeCAD as App  # noqa: E402
import Mesh  # noqa: E402
import Part  # noqa: E402

from freecad.SiteContextWB import (  # noqa: E402
    imagery,
    overpass_client,
    settings,
    site_builder,
)
from freecad.SiteContextWB.projection import (  # noqa: E402
    M_PER_DEG_LAT,
    bbox_from_center_radius,
    project_latlon,
)

EXPECTED_CHECKS = 16

_checks = []


def check(name):
    def deco(fn):
        _checks.append((name, fn))
        return fn

    return deco


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def approx(a, b, tol, msg):
    a = getattr(a, "Value", a)  # Quantity -> float
    b = getattr(b, "Value", b)
    if abs(a - b) > tol:
        raise AssertionError("%s (got %r, want %r +/- %r)" % (msg, a, b, tol))


# --- shared fixture ----------------------------------------------------------
class Fixture(object):
    """Shared constants: Trafalgar Square bbox, a synthetic OSM building,
    a tiny real PNG standing in for a stitched image, a temp dir."""

    def __init__(self):
        self.lat = 51.5077
        self.lon = -0.1281
        self.radius_m = 180
        self.bbox = bbox_from_center_radius(self.lat, self.lon, self.radius_m)
        self.tmp = tempfile.mkdtemp(prefix="sc_verify_")
        self.png_path = os.path.join(self.tmp, "stitched.png")
        from PIL import Image as PILImage

        PILImage.new("RGB", (32, 32), (10, 200, 30)).save(self.png_path)
        self.zoom, self.tile_range = imagery.choose_tile_range(
            *self.bbox, max_zoom=imagery.PROVIDERS["esri_world_imagery"]["max_zoom"]
        )

    def synthetic_osm(self):
        s, w, n, e = self.bbox
        clat = (s + n) / 2.0
        clon = (w + e) / 2.0
        d = 0.0003
        ring = [
            (clat - d, clon - d),
            (clat - d, clon + d),
            (clat + d, clon + d),
            (clat + d, clon - d),
            (clat - d, clon - d),
        ]
        return {
            "elements": [
                {
                    "type": "way",
                    "id": 4242,
                    "tags": {"building": "yes", "height": "12", "name": "VerifyHouse"},
                    "geometry": [{"lat": a, "lon": o} for a, o in ring],
                }
            ]
        }

    def fake_imagery(self):
        s, w, n, e = self.bbox
        x0, y0, x1, y1 = self.tile_range
        mw, mh, _c, _r = imagery.mosaic_geometry(self.tile_range)
        crop = imagery.bbox_pixel_crop(s, w, n, e, self.zoom, x0, y0, mw, mh)
        return {
            "image_path": self.png_path,
            "bounds_latlon": imagery.crop_bounds_latlon(self.zoom, x0, y0, crop),
            "provider_key": "esri_world_imagery",
            "provider_label": imagery.PROVIDERS["esri_world_imagery"]["label"],
            "zoom": self.zoom,
            "tile_range": self.tile_range,
            "tiles_fetched": imagery.tile_range_count(self.tile_range),
            "tiles_failed": 0,
            "cached": False,
            "width_px": crop[2] - crop[0],
            "height_px": crop[3] - crop[1],
            "attribution": imagery.imagery_attribution(
                "esri_world_imagery", self.zoom, 9
            ),
        }


def object_names(doc):
    return sorted(o.Name for o in doc.Objects)


def close_doc(doc):
    try:
        App.closeDocument(doc.Name)
    except Exception:  # noqa: BLE001
        pass


# --- 1-5: tile math ----------------------------------------------------------
@check("tile math: latlon_to_tile_xy hits known slippy-map values")
def c01(fx):
    x, y = imagery.latlon_to_tile_xy(0.0, 0.0, 0)
    approx(x, 0.5, 1e-12, "z0 equator/greenwich x")
    approx(y, 0.5, 1e-12, "z0 equator/greenwich y")
    x, y = imagery.latlon_to_tile_xy(0.0, 0.0, 1)
    approx(x, 1.0, 1e-12, "z1 origin x")
    approx(y, 1.0, 1e-12, "z1 origin y")
    # latitude clamps to the Mercator limit instead of blowing up
    y_clamped = imagery.latlon_to_tile_xy(89.9, 0.0, 10)[1]
    y_limit = imagery.latlon_to_tile_xy(imagery.MAX_MERCATOR_LAT, 0.0, 10)[1]
    approx(y_clamped, y_limit, 1e-12, "latitude clamp at +85.05112878")
    # Trafalgar against an independently written formula (log/tan form)
    lat_rad = math.radians(fx.lat)
    n = 2.0 ** 17
    exp_x = (fx.lon + 180.0) / 360.0 * n
    exp_y = (
        (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    )
    x, y = imagery.latlon_to_tile_xy(fx.lat, fx.lon, 17)
    approx(x, exp_x, 1e-9, "Trafalgar z17 x vs independent formula")
    approx(y, exp_y, 1e-9, "Trafalgar z17 y vs independent formula")


@check("tile math: tile_xy_to_latlon is the exact inverse (world corners)")
def c02(fx):
    lat, lon = imagery.tile_xy_to_latlon(0, 0, 5)
    approx(lat, imagery.MAX_MERCATOR_LAT, 1e-9, "tile (0,0) NW latitude")
    approx(lon, -180.0, 1e-12, "tile (0,0) NW longitude")
    lat, lon = imagery.tile_xy_to_latlon(32, 32, 5)
    approx(lat, -imagery.MAX_MERCATOR_LAT, 1e-9, "last tile SE latitude")
    approx(lon, 180.0, 1e-9, "last tile SE longitude")
    tx, ty = imagery.latlon_to_tile_xy(fx.lat, fx.lon, 17)
    itx, ity = int(math.floor(tx)), int(math.floor(ty))
    nw_lat, nw_lon = imagery.tile_xy_to_latlon(itx, ity, 17)
    rx, ry = imagery.latlon_to_tile_xy(nw_lat, nw_lon, 17)
    approx(rx, itx, 1e-6, "round-trip tile x")
    approx(ry, ity, 1e-6, "round-trip tile y")
    ok(nw_lat >= fx.lat and nw_lon <= fx.lon, "NW corner is not north-west of the point")


@check("tile math: bbox_to_tile_range fully covers the bbox")
def c03(fx):
    s, w, n, e = fx.bbox
    x0, y0, x1, y1 = imagery.bbox_to_tile_range(s, w, n, e, 17)
    ok(x1 >= x0 and y1 >= y0, "degenerate tile range")
    nw_lat, nw_lon = imagery.tile_xy_to_latlon(x0, y0, 17)
    se_lat, se_lon = imagery.tile_xy_to_latlon(x1 + 1, y1 + 1, 17)
    ok(nw_lon <= w and nw_lat >= n, "range does not cover the NW corner")
    ok(se_lon >= e and se_lat <= s, "range does not cover the SE corner")
    count = imagery.tile_range_count((x0, y0, x1, y1))
    ok(count == (x1 - x0 + 1) * (y1 - y0 + 1), "tile_range_count arithmetic")


@check("tile math: choose_tile_range never exceeds the 16-tile cap")
def c04(fx):
    s, w, n, e = bbox_from_center_radius(48.8610, 2.3360, 500)  # max radius
    zoom, rng = imagery.choose_tile_range(s, w, n, e, max_zoom=18)
    ok(zoom <= 18, "zoom above provider max")
    ok(imagery.tile_range_count(rng) <= imagery.MAX_TILES_PER_IMPORT,
       "500m radius needs more than the cap")
    s, w, n, e = bbox_from_center_radius(48.8610, 2.3360, 5000)  # API misuse
    zoom, rng = imagery.choose_tile_range(s, w, n, e, max_zoom=18)
    ok(imagery.tile_range_count(rng) <= imagery.MAX_TILES_PER_IMPORT,
       "5km bbox not contained by zoom descent/clamp")
    clamped = imagery._clamp_tile_range((10, 10, 12, 12), 4)  # 3x3 -> 2x2
    ok(imagery.tile_range_count(clamped) <= 4, "clamp did not shrink the range")
    ok(clamped[0] >= 10 and clamped[1] >= 10, "clamp is not centered")


@check("tile math: meters_per_pixel matches the Web-Mercator constants")
def c05(fx):
    approx(imagery.meters_per_pixel(0.0, 0), 156543.03392804097, 1e-6,
           "equator z0 (2*pi*R/256)")
    ratio = imagery.meters_per_pixel(0.0, 12) / imagery.meters_per_pixel(0.0, 13)
    approx(ratio, 2.0, 1e-12, "resolution doubles per zoom level")
    approx(imagery.meters_per_pixel(60.0, 10),
           imagery.meters_per_pixel(0.0, 10) * math.cos(math.radians(60.0)),
           1e-9, "cos(latitude) falloff")


# --- 6-9: stitch + plane geometry ---------------------------------------------
@check("stitch: mosaic/crop pixel geometry is consistent with the bbox")
def c06(fx):
    s, w, n, e = fx.bbox
    x0, y0, x1, y1 = fx.tile_range
    mw, mh, cols, rows = imagery.mosaic_geometry(fx.tile_range)
    ok(mw == cols * imagery.TILE_SIZE and mh == rows * imagery.TILE_SIZE,
       "mosaic not a whole number of tiles")
    ok(imagery.tile_pixel_offset(x0 + 1, y0 + 1, x0, y0) ==
       (imagery.TILE_SIZE, imagery.TILE_SIZE), "tile offset wrong")
    crop = imagery.bbox_pixel_crop(s, w, n, e, fx.zoom, x0, y0, mw, mh)
    ok(0 <= crop[0] < crop[2] <= mw and 0 <= crop[1] < crop[3] <= mh,
       "crop outside the mosaic: %r" % (crop,))
    bounds = imagery.crop_bounds_latlon(fx.zoom, x0, y0, crop)
    tol_deg = imagery.meters_per_pixel(fx.lat, fx.zoom) / M_PER_DEG_LAT * 1.5
    approx(bounds[0], s, tol_deg, "crop south vs bbox south")
    approx(bounds[1], w, tol_deg, "crop west vs bbox west")
    approx(bounds[2], n, tol_deg, "crop north vs bbox north")
    approx(bounds[3], e, tol_deg, "crop east vs bbox east")


@check("stitch: synthetic 2x2 tiles land in the right pixels (PIL)")
def c07(fx):
    from PIL import Image as PILImage

    tile_range = (100, 200, 101, 201)  # 2x2
    colors = {
        (100, 200): (255, 0, 0),
        (101, 200): (0, 255, 0),
        (100, 201): (0, 0, 255),
        (101, 201): (255, 255, 0),
    }
    tiles = {
        xy: PILImage.new("RGB", (imagery.TILE_SIZE, imagery.TILE_SIZE), rgb)
        for xy, rgb in colors.items()
    }
    ts = imagery.TILE_SIZE
    full = imagery._stitch_and_crop(tiles, tile_range, (0, 0, 2 * ts, 2 * ts))
    ok(full.size == (2 * ts, 2 * ts), "full mosaic size is %r" % (full.size,))
    ok(full.getpixel((10, 10)) == colors[(100, 200)], "NW tile misplaced")
    ok(full.getpixel((ts + 10, 10)) == colors[(101, 200)], "NE tile misplaced")
    ok(full.getpixel((10, ts + 10)) == colors[(100, 201)], "SW tile misplaced")
    ok(full.getpixel((ts + 10, ts + 10)) == colors[(101, 201)], "SE tile misplaced")
    cropped = imagery._stitch_and_crop(tiles, tile_range, (100, 100, 356, 356))
    ok(cropped.size == (256, 256), "cropped size is %r" % (cropped.size,))
    ok(cropped.getpixel((0, 0)) == colors[(100, 200)], "crop origin wrong")
    ok(cropped.getpixel((255, 255)) == colors[(101, 201)], "crop far corner wrong")


@check("plane: plane_metrics matches the equirectangular projection")
def c08(fx):
    lat0 = fx.lat
    lon0 = fx.lon
    s, w, n, e = fx.bbox
    x_size, y_size, cx, cy, z = imagery.plane_metrics(fx.bbox, lat0, lon0)
    gx0, gy0 = project_latlon(s, w, lat0, lon0)
    gx1, gy1 = project_latlon(n, e, lat0, lon0)
    approx(x_size, abs(gx1 - gx0) * 1000.0, 1e-6, "plane XSize")
    approx(y_size, abs(gy1 - gy0) * 1000.0, 1e-6, "plane YSize")
    approx(cx, 0.0, 1e-4, "bbox is centered on the origin, center x")
    approx(cy, 0.0, 1e-4, "bbox is centered on the origin, center y")
    approx(z, imagery.IMAGERY_Z_MM, 1e-12, "imagery z offset")
    exp_w_m = (e - w) * M_PER_DEG_LAT * math.cos(math.radians(lat0))
    approx(x_size, exp_w_m * 1000.0, 1e-3, "plane width vs hand-computed meters")


@check("plane: pixel scale and plane size agree within 1% (Mercator vs equirect)")
def c09(fx):
    fake = fx.fake_imagery()
    lat0 = fx.lat
    x_size, y_size, _cx, _cy, _z = imagery.plane_metrics(
        fake["bounds_latlon"], lat0, fx.lon
    )
    mpp = imagery.meters_per_pixel(lat0, fake["zoom"])
    width_from_px = fake["width_px"] * mpp * 1000.0
    rel = abs(width_from_px - x_size) / x_size
    ok(rel < 0.01, "width mismatch %.4f%% between tile scale and plane" % (rel * 100.0))


# --- 10-12: dialog choices -----------------------------------------------------
@check("settings: dialog choices round-trip through FreeCAD parameters")
def c10(fx):
    params = App.ParamGet(settings.PARAM_GROUP)
    for rem in ("RemString", "RemBool"):
        for key in ("OutputMode", "IncludeBuildings", "ImageryProvider"):
            try:
                getattr(params, rem)(key)
            except Exception:  # noqa: BLE001 - key absent / wrong type
                pass
    defaults = settings.load_settings()
    ok(defaults["output_mode"] == settings.MODE_2D_MAP,
       "first-run default is not the 2D map: %r" % defaults)
    ok(defaults["include_buildings"] is False, "first-run buildings default not off")
    ok(defaults["imagery_provider"] == imagery.DEFAULT_PROVIDER,
       "first-run provider default wrong")
    settings.save_settings(settings.MODE_3D_SITE, True, "osm_standard")
    saved = settings.load_settings()
    ok(saved["output_mode"] == settings.MODE_3D_SITE, "mode did not persist")
    ok(saved["include_buildings"] is True, "buildings choice did not persist")
    ok(saved["imagery_provider"] == "osm_standard", "provider did not persist")
    settings.save_settings(settings.MODE_2D_MAP, False, imagery.DEFAULT_PROVIDER)


@check("settings: fetch_plan honors the buildings checkbox and the mode")
def c11(fx):
    plan = settings.fetch_plan(settings.MODE_2D_MAP, False, True)
    ok(plan == {"imagery": True, "buildings": False, "terrain": False},
       "2D map, checkbox off: %r" % plan)
    plan = settings.fetch_plan(settings.MODE_2D_MAP, True, True)
    ok(plan["imagery"] and plan["buildings"] and not plan["terrain"],
       "2D map, checkbox on: %r" % plan)
    plan = settings.fetch_plan(settings.MODE_3D_SITE, False, True)
    ok(plan == {"imagery": False, "buildings": True, "terrain": True},
       "3D site must always keep buildings (v0.2 behavior): %r" % plan)
    plan = settings.fetch_plan(settings.MODE_3D_SITE, False, False)
    ok(not plan["terrain"] and plan["buildings"] and not plan["imagery"],
       "3D site without terrain: %r" % plan)


@check("attribution: provider credit, zoom, and tile count in the string")
def c12(fx):
    text = imagery.imagery_attribution("esri_world_imagery", 17, 9)
    ok("Esri" in text, "Esri credit missing: %r" % text)
    ok("GIS User Community" in text, "Esri community credit missing: %r" % text)
    ok("Zoom 17" in text and "9 tile(s)" in text, "provenance missing: %r" % text)
    ok(imagery.PROVIDERS["esri_world_imagery"]["terms_url"] in text,
       "Esri terms URL missing")
    cached = imagery.imagery_attribution("osm_standard", 18, 0, cached=True)
    ok("OpenStreetMap contributors" in cached, "OSM credit missing: %r" % cached)
    ok("CC BY-SA" in cached, "OSM tile cartography license missing: %r" % cached)
    ok("0 network requests" in cached, "cache provenance missing: %r" % cached)


# --- 13-15: build pipeline -----------------------------------------------------
@check("build: legacy 3D site call is unchanged (building + ground + georef)")
def c13(fx):
    doc, stats = site_builder.build_site(
        App, Part, Mesh, "SCVerify3D", fx.bbox, "verify 3d", fx.synthetic_osm()
    )
    try:
        names = object_names(doc)
        ok("bldg_4242" in names, "building object missing: %r" % names)
        ok("GroundPlane" in names, "ground plane missing: %r" % names)
        ok("SiteImagery" not in names, "unexpected imagery plane: %r" % names)
        ok(stats["built"] == 1 and stats["fetched"] == 1, "stats: %r" % stats)
        ok(stats["include_buildings"] is True, "default include_buildings flipped")
        ok(stats["imagery"] is None, "unexpected imagery stats")
        group = doc.getObject("SiteContext")
        ok(group is not None and "OriginLatitude" in group.PropertiesList,
           "georef properties missing")
        approx(group.OriginLatitude, (fx.bbox[0] + fx.bbox[2]) / 2.0, 1e-9,
               "origin latitude")
        ok("OpenStreetMap contributors" in doc.Comment, "ODbL attribution missing")
        bldg = doc.getObject("bldg_4242")
        ok(bldg.Shape.isValid() and bldg.Shape.Volume > 0, "building solid invalid")
    finally:
        close_doc(doc)


@check("build: 2D map without the checkbox - Overpass not called, no buildings")
def c14(fx):
    calls = []
    real_fetch = overpass_client.fetch_overpass_bbox
    overpass_client.fetch_overpass_bbox = lambda *a, **k: calls.append((a, k)) or fx.synthetic_osm()
    try:
        plan = settings.fetch_plan(settings.MODE_2D_MAP, False, True)
        osm_data = (
            overpass_client.fetch_overpass_bbox(*fx.bbox)
            if plan["buildings"]
            else {"elements": []}
        )
        ok(calls == [], "Overpass was called with the checkbox off")
        fake = fx.fake_imagery()
        doc, stats = site_builder.build_site(
            App, Part, Mesh, "SCVerifyMap", fx.bbox, "verify map", osm_data,
            include_buildings=plan["buildings"],
            imagery=fake,
            ground_plane=not plan["imagery"],
        )
        try:
            names = object_names(doc)
            ok(not any(n.startswith("bldg") for n in names),
               "buildings appeared with the checkbox off: %r" % names)
            ok("GroundPlane" not in names, "ground box in 2D map mode: %r" % names)
            plane = doc.getObject("SiteImagery")
            ok(plane is not None and plane.TypeId == "Image::ImagePlane",
               "imagery plane missing: %r" % names)
            x_size, y_size, cx, cy, z = imagery.plane_metrics(
                fake["bounds_latlon"], fx.lat, fx.lon
            )
            approx(plane.XSize, x_size, 1e-6, "plane XSize vs plane_metrics")
            approx(plane.YSize, y_size, 1e-6, "plane YSize vs plane_metrics")
            approx(plane.Placement.Base.x, cx, 1e-6, "plane center x")
            approx(plane.Placement.Base.y, cy, 1e-6, "plane center y")
            approx(plane.Placement.Base.z, imagery.IMAGERY_Z_MM, 1e-9, "plane z")
            # PropertyFile copies the image into the document's transient
            # dir (that copy is what lands inside a saved .FCStd), so
            # compare content, not the path string.
            ok(os.path.basename(plane.ImageFile) ==
               os.path.basename(fake["image_path"]), "plane image name wrong")
            ok(os.path.isfile(plane.ImageFile), "plane image copy missing")
            ok(os.path.getsize(plane.ImageFile) ==
               os.path.getsize(fake["image_path"]),
               "plane image copy differs from the stitched file")
            ok("Esri" in plane.Attribution, "plane attribution property missing")
            group = doc.getObject("SiteContext")
            ok("ImageryAttribution" in group.PropertiesList,
               "group imagery attribution missing")
            ok("Attribution" not in group.PropertiesList,
               "OSM building attribution present without buildings")
            ok("Esri" in doc.Comment, "imagery attribution not in doc.Comment")
            ok(stats["built"] == 0 and stats["fetched"] == 0,
               "building stats not zero: %r" % stats)
            ok(stats["imagery"]["zoom"] == fx.zoom, "imagery stats missing")
        finally:
            close_doc(doc)
    finally:
        overpass_client.fetch_overpass_bbox = real_fetch


@check("build: 2D map with the checkbox - Overpass called, buildings on the map")
def c15(fx):
    calls = []
    real_fetch = overpass_client.fetch_overpass_bbox
    overpass_client.fetch_overpass_bbox = lambda *a, **k: calls.append((a, k)) or fx.synthetic_osm()
    try:
        plan = settings.fetch_plan(settings.MODE_2D_MAP, True, True)
        osm_data = (
            overpass_client.fetch_overpass_bbox(*fx.bbox)
            if plan["buildings"]
            else {"elements": []}
        )
        ok(len(calls) == 1, "Overpass not called exactly once: %r" % calls)
        doc, stats = site_builder.build_site(
            App, Part, Mesh, "SCVerifyMapB", fx.bbox, "verify map+b", osm_data,
            include_buildings=plan["buildings"],
            imagery=fx.fake_imagery(),
            ground_plane=not plan["imagery"],
        )
        try:
            names = object_names(doc)
            ok("bldg_4242" in names, "building missing on the map: %r" % names)
            ok("SiteImagery" in names, "imagery plane missing: %r" % names)
            ok("GroundPlane" not in names, "ground box in 2D map mode: %r" % names)
            ok(stats["built"] == 1, "stats: %r" % stats)
            ok("OpenStreetMap contributors" in doc.Comment,
               "ODbL attribution missing with buildings on")
            group = doc.getObject("SiteContext")
            ok("Attribution" in group.PropertiesList,
               "OSM attribution missing with buildings on")
            bldg = doc.getObject("bldg_4242")
            ok(bldg.Shape.isValid() and bldg.Shape.Volume > 0,
               "building solid invalid")
        finally:
            close_doc(doc)
    finally:
        overpass_client.fetch_overpass_bbox = real_fetch


# --- 16: dialog module ----------------------------------------------------------
@check("dialog: add_location_dialog imports headless (syntax/wiring smoke)")
def c16(fx):
    import freecad.SiteContextWB.add_location_dialog as dlg

    ok(hasattr(dlg, "AddLocationDialog"), "dialog class missing")
    ok("Esri" in dlg.ATTRIBUTION_FOOTER, "imagery attribution missing from footer")
    ok("16 tiles" in dlg.ATTRIBUTION_FOOTER, "tile cap missing from footer")


def main():
    fx = Fixture()
    passed = 0
    failures = []
    for idx, (name, fn) in enumerate(_checks, 1):
        try:
            fn(fx)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures.append((idx, name, exc))
            print("[FAIL %2d] %s" % (idx, name))
            traceback.print_exc()
        else:
            passed += 1
            print("[ ok  %2d] %s" % (idx, name))
    total = passed + len(failures)
    print("-" * 64)
    print("%d/%d checks pass" % (passed, total))
    if total != EXPECTED_CHECKS:
        print("WARNING: ran %d checks, expected %d -- update EXPECTED_CHECKS"
              % (total, EXPECTED_CHECKS))
    if failures:
        print("FAILURES:")
        for idx, name, exc in failures:
            print("  %2d. %s: %s" % (idx, name, exc))
        return 1
    return 0


# Not guarded by __name__ == "__main__": stock freecadcmd (for example the
# conda-forge 1.1.0 build) does not set __name__ that way, so a guarded
# harness silently runs zero checks and still exits 0. Run unconditionally;
# os._exit propagates the code without tripping freecadcmd's SystemExit
# handling, and the flush beats freecadcmd's buffered stdout.
rc = main()
sys.stdout.flush()
os._exit(rc)
