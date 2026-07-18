# SPDX-License-Identifier: MIT
"""2D map mode: fetch Web-Mercator map/satellite tiles for the Add-Location
bbox, stitch them into one image, and hand site_builder.py enough geometry
to place it as a flat, correctly-scaled Image::ImagePlane.

Tile sources ("providers") are small dicts in PROVIDERS. The default is the
OpenStreetMap standard tile layer: its tile usage policy explicitly allows
this kind of light, cached, attributed use. Esri World Imagery
(satellite/aerial) is offered as an opt-in alternative; its tiles are
publicly served but Esri's terms are written around ArcGIS products, so we
do not make it the default. Provider discipline, same ethos as the rest of
the addon:

  - A descriptive User-Agent on every request (both providers require one;
    OSM's tile usage policy names it explicitly, Esri's terms ask for an
    identifiable client).
  - Small fixed-size requests only: the tile count per import is hard-capped
    (MAX_TILES_PER_IMPORT, e.g. 4x4). choose_tile_range() picks the highest
    zoom whose coverage of the bbox fits the cap, so a bigger radius simply
    gets a coarser zoom, never more tiles. No bulk downloading.
  - A short pause between tile requests (TILE_REQUEST_GAP_S).
  - The stitched image is cached on disk per (provider, zoom, bbox), so a
    given area is fetched from the network at most once. OSM's tile policy
    explicitly encourages caching.
  - Attribution: every provider's credit string travels with the result and
    is written into the document (plane object properties + doc.Comment) by
    site_builder.py, and into the README. A short credit line is also
    stamped into the bottom-left corner of the stitched image itself, so
    the credit stays visible in the 3D view and in any screenshot.

Scale/placement model: the stitched mosaic is cropped to the exact pixel
bounds of the requested bbox, so the finished image spans a known lat/lon
rectangle (bounds_latlon in the result). site_builder.py projects that
rectangle with the addon's usual equirectangular approximation and sizes the
ImagePlane to it. Within the addon's supported radius (<=500m) the Web
Mercator <-> equirectangular mismatch across the image is well under a
percent; see README "Accuracy limits".

ImagePlane contract (verified against FreeCAD's ViewProviderImagePlane.cpp,
both the current main branch and the 1.x doxygen snapshot): the textured
quad is CENTERED on Placement.Base, spans XSize x YSize mm in local XY, and
displays the image upright (BitmapFactory::convert flips the QImage rows
into Coin's bottom-up texture order, so file row 0 renders at the +Y edge).
Our stitched row 0 is the northern edge, so a plain center placement comes
out north-up with no rotation.

PIL (bundled with FreeCAD 1.x, verified: Pillow 12.0.0 under freecadcmd
1.1.1) does the stitching. It is imported lazily inside the stitch function
so the rest of the module, and the whole 3D pipeline, works without it.

Only fetch_mosaic()/fetch_tile() touch the network. Everything else is pure
math or local image work and is covered by verify/headless_regression.py.
"""
import io
import math
import os
import time
import urllib.error
import urllib.request

from .projection import project_latlon

TILE_SIZE = 256
MAX_TILES_PER_IMPORT = 16  # hard cap per import (4x4), politeness
TILE_REQUEST_GAP_S = 0.15
EARTH_RADIUS_M = 6378137.0
MAX_MERCATOR_LAT = 85.05112878
IMAGERY_Z_MM = -10.0  # 1cm below z=0: no z-fighting with building bases
IMAGERY_USER_AGENT = (
    "FreeCAD-SiteContext-addon/0.3 (+https://github.com/mathmati; contact via GitHub)"
)

PROVIDERS = {
    "osm_standard": {
        "label": "Map (OpenStreetMap standard tiles)",
        "url_template": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "max_zoom": 19,
        "attribution": (
            "(c) OpenStreetMap contributors; map data ODbL 1.0, "
            "tile cartography CC BY-SA 2.0."
        ),
        "credit": "(c) OpenStreetMap contributors",
        "terms_url": "https://operations.osmfoundation.org/policies/tiles/",
    },
    "esri_world_imagery": {
        "label": "Satellite (Esri World Imagery)",
        "url_template": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        # z18 is the highest zoom with near-worldwide coverage; z19 exists
        # only in some metro areas. The attribution string is the service's
        # own copyrightText (REST metadata, checked 2026-07).
        "max_zoom": 18,
        "attribution": (
            "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community."
        ),
        "credit": "Source: Esri, Vantor, Earthstar Geographics",
        "terms_url": "https://goto.arcgisonline.com/maps/World_Imagery",
    },
}
DEFAULT_PROVIDER = "osm_standard"


class ImageryError(RuntimeError):
    pass


# ------------------------------------------------------------ pure tile math
def latlon_to_tile_xy(lat, lon, zoom):
    """Fractional Web-Mercator tile coordinates (slippy-map convention):
    x grows east from lon -180, y grows south from the Mercator limit
    (+85.05112878). Latitudes are clamped to the Mercator limit."""
    lat = max(-MAX_MERCATOR_LAT, min(MAX_MERCATOR_LAT, lat))
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def tile_xy_to_latlon(x, y, zoom):
    """Lat/lon of the NORTH-WEST corner of fractional tile position (x, y).
    Exact inverse of latlon_to_tile_xy."""
    n = 2.0 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon


def meters_per_pixel(lat, zoom):
    """Ground meters per image pixel at the given latitude/zoom (Web
    Mercator is conformal, so this is the same in both axes locally)."""
    lat = max(-MAX_MERCATOR_LAT, min(MAX_MERCATOR_LAT, lat))
    return math.cos(math.radians(lat)) * 2.0 * math.pi * EARTH_RADIUS_M / (
        TILE_SIZE * 2.0 ** zoom
    )


def bbox_to_tile_range(s, w, n, e, zoom):
    """Inclusive integer tile range (x0, y0, x1, y1) covering the bbox at
    the given zoom. y0 is the northern row. Rows are clamped to the valid
    [0, 2^zoom - 1] band: a bbox edge exactly at the Mercator latitude
    limit lands on fractional row 2^zoom, whose floor would be a row of
    tiles that does not exist (every request 404s -> an all-gray strip).
    Columns are NOT clamped -- a bbox crossing the antimeridian legitimately
    spans x >= 2^zoom, and fetch_mosaic() wraps x modulo 2^zoom per
    request."""
    n_tiles = 2 ** int(zoom)
    fx0, fy0 = latlon_to_tile_xy(n, w, zoom)  # NW corner: min x, min y
    fx1, fy1 = latlon_to_tile_xy(s, e, zoom)  # SE corner: max x, max y
    return (
        int(math.floor(fx0)),
        max(0, min(n_tiles - 1, int(math.floor(fy0)))),
        int(math.floor(fx1)),
        max(0, min(n_tiles - 1, int(math.floor(fy1)))),
    )


def tile_range_count(tile_range):
    x0, y0, x1, y1 = tile_range
    return (x1 - x0 + 1) * (y1 - y0 + 1)


def _clamp_tile_range(tile_range, max_tiles):
    """Center-crop a tile range so it holds at most max_tiles tiles. Only
    reachable for bboxes far beyond the dialog's 100-500m radius (even zoom
    1 would overflow the cap); keeps fetch_mosaic() honest for API callers."""
    x0, y0, x1, y1 = tile_range
    side = int(math.sqrt(max_tiles))
    cols = min(x1 - x0 + 1, side)
    rows = min(y1 - y0 + 1, max(1, max_tiles // cols))
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    nx0 = cx - cols // 2
    ny0 = cy - rows // 2
    return (nx0, ny0, nx0 + cols - 1, ny0 + rows - 1)


def choose_tile_range(s, w, n, e, max_zoom, max_tiles=MAX_TILES_PER_IMPORT):
    """Highest zoom (<= max_zoom) whose tile coverage of the bbox fits the
    max_tiles cap. Returns (zoom, (x0, y0, x1, y1)). If no zoom fits (bbox
    far beyond the supported radius), returns zoom 1 center-clamped to the
    cap -- the imagery then covers only the bbox center, and bounds_latlon
    in the result says so honestly."""
    for zoom in range(int(max_zoom), 0, -1):
        tile_range = bbox_to_tile_range(s, w, n, e, zoom)
        if tile_range_count(tile_range) <= max_tiles:
            return zoom, tile_range
    return 1, _clamp_tile_range(bbox_to_tile_range(s, w, n, e, 1), max_tiles)


def mosaic_geometry(tile_range):
    """(width_px, height_px, cols, rows) of the stitched mosaic."""
    x0, y0, x1, y1 = tile_range
    cols = x1 - x0 + 1
    rows = y1 - y0 + 1
    return cols * TILE_SIZE, rows * TILE_SIZE, cols, rows


def tile_pixel_offset(x, y, x0, y0):
    """Pixel offset of tile (x, y) within a mosaic whose origin tile is
    (x0, y0)."""
    return (x - x0) * TILE_SIZE, (y - y0) * TILE_SIZE


def bbox_pixel_crop(s, w, n, e, zoom, x0, y0, mosaic_w, mosaic_h):
    """Pixel bounds (px0, py0, px1, py1) of the bbox within the mosaic,
    clamped to the mosaic. Row py0 is the northern edge of the bbox."""
    fx0, fy0 = latlon_to_tile_xy(n, w, zoom)
    fx1, fy1 = latlon_to_tile_xy(s, e, zoom)
    px0 = int(round((fx0 - x0) * TILE_SIZE))
    py0 = int(round((fy0 - y0) * TILE_SIZE))
    px1 = int(round((fx1 - x0) * TILE_SIZE))
    py1 = int(round((fy1 - y0) * TILE_SIZE))
    px0 = max(0, min(mosaic_w - 1, px0))
    py0 = max(0, min(mosaic_h - 1, py0))
    px1 = max(px0 + 1, min(mosaic_w, px1))
    py1 = max(py0 + 1, min(mosaic_h, py1))
    return px0, py0, px1, py1


def crop_bounds_latlon(zoom, x0, y0, crop):
    """Exact (s, w, n, e) the cropped image covers, by inverse-mapping the
    crop's pixel corners back to lat/lon. In the common case this equals the
    requested bbox to within a pixel; after a center-clamp it is smaller."""
    px0, py0, px1, py1 = crop
    n, w = tile_xy_to_latlon(x0 + px0 / TILE_SIZE, y0 + py0 / TILE_SIZE, zoom)
    s, e = tile_xy_to_latlon(x0 + px1 / TILE_SIZE, y0 + py1 / TILE_SIZE, zoom)
    return s, w, n, e


def plane_metrics(bounds, lat0, lon0, z_mm=IMAGERY_Z_MM):
    """(x_size_mm, y_size_mm, center_x_mm, center_y_mm, z_mm) for the
    ImagePlane covering bounds=(s, w, n, e), projected with the addon's
    equirectangular approximation around (lat0, lon0)."""
    s, w, n, e = bounds
    x0, y0 = project_latlon(s, w, lat0, lon0)  # SW corner, meters
    x1, y1 = project_latlon(n, e, lat0, lon0)  # NE corner, meters
    return (
        abs(x1 - x0) * 1000.0,
        abs(y1 - y0) * 1000.0,
        (x0 + x1) / 2.0 * 1000.0,
        (y0 + y1) / 2.0 * 1000.0,
        z_mm,
    )


# ------------------------------------------------------------- attribution
def imagery_attribution(provider_key, zoom, tiles_fetched, cached=False):
    """Attribution/provenance string for one imagery import, written into
    the document by site_builder.py."""
    provider = PROVIDERS[provider_key]
    source = "local cache, 0 network requests" if cached else (
        f"{tiles_fetched} tile(s) fetched"
    )
    return (
        f"Imagery: {provider['label']}. {provider['attribution']} "
        f"Zoom {zoom}, {source}. Terms: {provider['terms_url']}"
    )


# ------------------------------------------------------------------ caching
def default_cache_dir():
    import FreeCAD as App

    base = App.getUserAppDataDir() if hasattr(App, "getUserAppDataDir") else "."
    path = os.path.join(base, "SiteContextCache")
    os.makedirs(path, exist_ok=True)
    return path


def imagery_cache_path(provider_key, zoom, bbox, cache_dir):
    s, w, n, e = bbox
    slug = "imagery_{}_z{}_{:.5f}_{:.5f}_{:.5f}_{:.5f}.png".format(
        provider_key, zoom, s, w, n, e
    )
    return os.path.join(cache_dir, slug)


# ------------------------------------------------------------------ network
def fetch_tile(provider, zoom, x, y, timeout=20, retries=1):
    """Fetch one tile's image bytes, or None on failure (caller substitutes
    a placeholder). One retry after a short pause; no aggressive hammering."""
    url = provider["url_template"].format(z=zoom, x=x, y=y)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": IMAGERY_USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception:  # noqa: BLE001 - any network/HTTP error -> retry/None
            if attempt < retries:
                time.sleep(0.5)
    return None


def _stitch_and_crop(tile_images, tile_range, crop):
    """Paste {tile (x,y): PIL.Image} into one mosaic and crop it. Returns
    the cropped PIL.Image. PIL is imported lazily so the module loads (and
    the whole 3D pipeline keeps working) on FreeCAD builds without Pillow."""
    try:
        from PIL import Image as PILImage
    except ImportError as exc:
        raise ImageryError(
            "2D map mode needs Pillow (PIL), which is bundled with official "
            "FreeCAD 1.x builds; this build does not have it"
        ) from exc

    mosaic_w, mosaic_h, _cols, _rows = mosaic_geometry(tile_range)
    x0, y0, _x1, _y1 = tile_range
    mosaic = PILImage.new("RGB", (mosaic_w, mosaic_h), (200, 200, 200))
    for (x, y), img in tile_images.items():
        mosaic.paste(img, tile_pixel_offset(x, y, x0, y0))
    return mosaic.crop(crop)


def stamp_credit(img, text):
    """Draw the provider credit into the bottom-left corner of the image,
    white text on a dark band, so the credit stays visible in the 3D view
    and in screenshots (both providers' terms ask for visible attribution).
    Overlays pixels in place; the image size is untouched, so the pixel
    geometry the ImagePlane is scaled from stays exact. Pillow's built-in
    bitmap font is used: no font files, works everywhere PIL does."""
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img, "RGBA")
    left, top, right, bottom = draw.textbbox((0, 0), text)
    pad = 3
    band_h = (bottom - top) + 2 * pad
    band_w = (right - left) + 2 * pad
    y0 = max(0, img.height - band_h)
    draw.rectangle(
        (0, y0, min(img.width, band_w), img.height), fill=(0, 0, 0, 160)
    )
    draw.text((pad, y0 + pad - top), text, fill=(255, 255, 255, 255))
    return img


def fetch_mosaic(
    provider_key,
    s,
    w,
    n,
    e,
    cache_dir=None,
    max_tiles=MAX_TILES_PER_IMPORT,
    progress_cb=None,
    fetch_fn=None,
):
    """Fetch + stitch + crop the imagery covering bbox (s, w, n, e).

    Returns a dict: image_path (PNG on disk), bounds_latlon, provider info,
    zoom, tile counts, pixel size, cached flag. Never exceeds max_tiles
    network requests, and zero requests on a cache hit. fetch_fn is
    injectable for tests; the default is fetch_tile.
    """
    if provider_key not in PROVIDERS:
        raise ImageryError(f"unknown imagery provider: {provider_key}")
    provider = PROVIDERS[provider_key]
    fetch_fn = fetch_fn or fetch_tile
    if cache_dir is None:
        cache_dir = default_cache_dir()

    zoom, tile_range = choose_tile_range(
        s, w, n, e, provider["max_zoom"], max_tiles
    )
    x0, y0, x1, y1 = tile_range
    mosaic_w, mosaic_h, cols, rows = mosaic_geometry(tile_range)
    crop = bbox_pixel_crop(s, w, n, e, zoom, x0, y0, mosaic_w, mosaic_h)
    bounds = crop_bounds_latlon(zoom, x0, y0, crop)
    width_px = crop[2] - crop[0]
    height_px = crop[3] - crop[1]
    cache_path = imagery_cache_path(provider_key, zoom, (s, w, n, e), cache_dir)

    if os.path.exists(cache_path):
        if progress_cb:
            progress_cb(f"using cached imagery: {cache_path}")
        out_path = cache_path
        tiles_fetched = 0
        tiles_failed = 0
        cached = True
    else:
        try:
            from PIL import Image as PILImage
        except ImportError as exc:
            raise ImageryError(
                "2D map mode needs Pillow (PIL), which is bundled with "
                "official FreeCAD 1.x builds; this build does not have it"
            ) from exc

        total = tile_range_count(tile_range)
        tile_images = {}
        tiles_failed = 0
        i = 0
        last_call = 0.0
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                i += 1
                if progress_cb:
                    progress_cb(
                        f"fetching imagery tile {i}/{total} (z{zoom}) "
                        f"from {provider['label']} ..."
                    )
                elapsed = time.time() - last_call
                if last_call and elapsed < TILE_REQUEST_GAP_S:
                    time.sleep(TILE_REQUEST_GAP_S - elapsed)
                # Wrap x for bboxes crossing the antimeridian: column
                # 2^zoom is column 0 one world further east.
                raw = fetch_fn(provider, zoom, tx % (2 ** zoom), ty)
                last_call = time.time()
                if raw is None:
                    tiles_failed += 1
                    continue
                try:
                    tile_images[(tx, ty)] = PILImage.open(io.BytesIO(raw)).convert("RGB")
                except Exception:  # noqa: BLE001 - undecodable tile -> placeholder
                    tiles_failed += 1
        tiles_fetched = total - tiles_failed
        if tiles_fetched == 0:
            raise ImageryError(
                f"no imagery tiles could be fetched from "
                f"{provider['label']} ({total} attempted); offline or "
                "provider unavailable -- nothing was cached, try again later"
            )
        cropped = _stitch_and_crop(tile_images, tile_range, crop)
        stamp_credit(cropped, provider.get("credit", provider["attribution"]))
        os.makedirs(cache_dir, exist_ok=True)
        # A mosaic with missing (gray) tiles must NOT land in the cache:
        # the cache is keyed only by bbox, so a transient failure would be
        # frozen in forever ("cached=True, 0 requests" on every later
        # import). Save partial results under a deterministic side path
        # instead, so the next import retries the network.
        if tiles_failed:
            out_path = cache_path[:-4] + "_partial.png"
        else:
            out_path = cache_path
        cropped.save(out_path, format="PNG")
        cached = False
        if progress_cb and tiles_failed:
            progress_cb(
                f"{tiles_failed} tile(s) unavailable at z{zoom}; shown as "
                "gray (result not cached; a later import will retry)"
            )

    return {
        "image_path": out_path,
        "bounds_latlon": bounds,
        "provider_key": provider_key,
        "provider_label": provider["label"],
        "zoom": zoom,
        "tile_range": tile_range,
        "tiles_fetched": tiles_fetched,
        "tiles_failed": tiles_failed,
        "cached": cached,
        "width_px": width_px,
        "height_px": height_px,
        "attribution": imagery_attribution(
            provider_key, zoom, tiles_fetched, cached=cached
        ),
    }
