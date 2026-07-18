# SPDX-License-Identifier: MIT
"""verify/live_imagery_smoke.py -- MANUAL/NETWORK smoke test for 2D map mode.

This is NOT part of the automated suite (verify/headless_regression.py runs
fully offline). It makes one real, small imagery fetch -- a 100m-radius bbox
around Trafalgar Square, so at most 16 tiles -- stitches it, builds the
Image::ImagePlane, and prints what it did. Run it by hand to prove the live
path against the real tile services:

    freecadcmd verify/live_imagery_smoke.py            # Esri World Imagery
    SC_SMOKE_PROVIDER=osm_standard freecadcmd ...      # OSM standard tiles

Be polite: run it once or twice, not in a loop. The stitched image lands in
the addon cache (FreeCAD user-data dir, SiteContextCache/), so a second run
for the same bbox makes zero network requests.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
import freecad

freecad.__path__ = [os.path.join(_REPO_ROOT, "freecad")] + list(freecad.__path__)
for _mod in [
    m
    for m in list(sys.modules)
    if m == "freecad.SiteContextWB" or m.startswith("freecad.SiteContextWB.")
]:
    del sys.modules[_mod]

import FreeCAD as App  # noqa: E402
import Mesh  # noqa: E402
import Part  # noqa: E402

from freecad.SiteContextWB import imagery, settings, site_builder  # noqa: E402
from freecad.SiteContextWB.projection import bbox_from_center_radius  # noqa: E402

LAT, LON, RADIUS_M = 51.5077, -0.1281, 100  # Trafalgar Square, small bbox


def main():
    provider_key = os.environ.get("SC_SMOKE_PROVIDER", imagery.DEFAULT_PROVIDER)
    if provider_key not in imagery.PROVIDERS:
        print("unknown provider %r; choices: %s"
              % (provider_key, ", ".join(sorted(imagery.PROVIDERS))))
        return 2

    s, w, n, e = bbox_from_center_radius(LAT, LON, RADIUS_M)
    print("bbox (S,W,N,E) = (%.6f, %.6f, %.6f, %.6f)" % (s, w, n, e))
    print("provider       = %s" % imagery.PROVIDERS[provider_key]["label"])

    def progress(msg):
        print("  fetch: %s" % msg)

    result = imagery.fetch_mosaic(
        provider_key, s, w, n, e,
        cache_dir=imagery.default_cache_dir(),
        progress_cb=progress,
    )
    print("zoom           = %d (auto, cap %d tiles)"
          % (result["zoom"], imagery.MAX_TILES_PER_IMPORT))
    print("tile range     = %r -> %d tile(s), %d failed, cached=%s"
          % (result["tile_range"],
             imagery.tile_range_count(result["tile_range"]),
             result["tiles_failed"], result["cached"]))
    print("image          = %d x %d px -> %s"
          % (result["width_px"], result["height_px"], result["image_path"]))
    bs, bw, bn, be = result["bounds_latlon"]
    print("image bounds   = (%.6f, %.6f, %.6f, %.6f)" % (bs, bw, bn, be))
    mpp = imagery.meters_per_pixel(LAT, result["zoom"])
    print("scale          = %.4f m/px at latitude %.4f" % (mpp, LAT))

    doc, stats = site_builder.build_site(
        App, Part, Mesh, "SiteContext_Smoke", (s, w, n, e),
        "imagery smoke", {"elements": []},
        include_buildings=False,
        imagery=result,
        ground_plane=False,
    )
    plane = doc.getObject("SiteImagery")
    print("plane          = %s, XSize %.1f m x YSize %.1f m, center (%.1f, %.1f, %.1f) mm"
          % (plane.TypeId, plane.XSize.Value / 1000.0, plane.YSize.Value / 1000.0,
             plane.Placement.Base.x, plane.Placement.Base.y, plane.Placement.Base.z))
    print("plane image    = %s" % plane.ImageFile)
    print("attribution    = %s" % plane.Attribution)

    out_dir = os.path.join(_HERE, "out-smoke")
    os.makedirs(out_dir, exist_ok=True)
    fcstd = os.path.join(out_dir, "SiteContext_Smoke_%s.FCStd" % provider_key)
    doc.saveAs(fcstd)
    print("saved          = %s" % fcstd)
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
