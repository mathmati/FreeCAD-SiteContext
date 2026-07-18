# SPDX-License-Identifier: MIT
"""Core "Add Location" site-generation pipeline.

Ported and extended from the v0 prototype (sitecontext_proto.py). Given
Overpass building data (ways AND, new in v0.2, multipolygon relations)
plus an optional elevation grid, builds a FreeCAD document containing
extruded building solids, a ground/terrain surface, and georeference
metadata.

This module is FreeCAD-API-heavy (Part/Mesh/document mutation) and per the
three headless-FreeCAD gotchas documented in sitecontext_proto.py/README.md
must run on the main thread (not a background QThread) -- callers doing
GUI work should pump QApplication.processEvents() periodically via
progress_cb rather than moving this to a thread.
"""
import math
import re

from .projection import project_latlon
from .terrain import build_terrain_mesh, relief as terrain_relief, RELIEF_THRESHOLD_M

DEFAULT_HEIGHT_M = 8.0
LEVEL_HEIGHT_M = 3.0
GROUND_THICKNESS_M = 0.1

OSM_ATTRIBUTION = (
    "Building footprints © OpenStreetMap contributors, "
    "licensed under the Open Database License (ODbL) v1.0. "
    "https://www.openstreetmap.org/copyright"
)
ELEVATION_ATTRIBUTION = (
    "Terrain elevation: SRTM 90m via the public api.opentopodata.org service."
)
PROJECTION_NOTE = (
    "Projection: equirectangular (plate-carree) approximation around the "
    "stored origin. Meters are exact AT the origin; distortion grows with "
    "distance from it and with absolute latitude. Not a conformal or "
    "equal-area projection -- do not use for anything beyond local, "
    "small-area (sub-kilometer) massing models. See README.md."
)


def parse_height_m(tags):
    """Return (height_m, source) where source in {"height", "levels", "default"}."""
    raw_height = tags.get("height")
    if raw_height:
        match = re.search(r"[-+]?\d*\.?\d+", raw_height)
        if match:
            try:
                return float(match.group()), "height"
            except ValueError:
                pass

    raw_levels = tags.get("building:levels")
    if raw_levels:
        match = re.search(r"[-+]?\d*\.?\d+", raw_levels)
        if match:
            try:
                levels = float(match.group())
                if levels > 0:
                    return levels * LEVEL_HEIGHT_M, "levels"
            except ValueError:
                pass

    return DEFAULT_HEIGHT_M, "default"


def _ring_to_wire(Part, FreeCAD, geometry, lat0, lon0):
    """Project an Overpass geometry array (list of {"lat","lon"} dicts, or
    None entries for unresolved nodes) into a closed Part.Wire, or None if
    the ring is degenerate. Shared by both way and relation-member rings.
    """
    if not geometry or len(geometry) < 4:
        return None
    if any(pt is None for pt in geometry):
        return None

    pts_m = [project_latlon(pt["lat"], pt["lon"], lat0, lon0) for pt in geometry]
    dedup = [pts_m[0]]
    for p in pts_m[1:]:
        if abs(p[0] - dedup[-1][0]) > 1e-6 or abs(p[1] - dedup[-1][1]) > 1e-6:
            dedup.append(p)
    if len(dedup) < 4:
        return None
    if abs(dedup[0][0] - dedup[-1][0]) > 1e-6 or abs(dedup[0][1] - dedup[-1][1]) > 1e-6:
        dedup.append(dedup[0])

    vecs = [FreeCAD.Vector(x * 1000.0, y * 1000.0, 0.0) for x, y in dedup]
    try:
        return Part.makePolygon(vecs)
    except Exception:  # noqa: BLE001 - degenerate/self-intersecting ring
        return None


def _build_way_solid(Part, FreeCAD, element, lat0, lon0):
    tags = element.get("tags", {})
    wire = _ring_to_wire(Part, FreeCAD, element.get("geometry"), lat0, lon0)
    if wire is None:
        return None
    face = Part.Face(wire)
    if not face.isValid():
        return None
    height_m, source = parse_height_m(tags)
    solid = face.extrude(FreeCAD.Vector(0, 0, height_m * 1000.0))
    if not solid.isValid() or solid.Volume <= 0:
        return None
    return solid, height_m, source


def _build_relation_solids(Part, FreeCAD, element, lat0, lon0):
    """Build one solid per "outer" ring in a multipolygon relation, cutting
    away any "inner" ring (hole) whose centroid falls inside that outer's
    footprint.

    v0.2 simplification (documented, not silently): this assumes each
    outer/inner MEMBER WAY is already an individually closed ring, as
    Overpass returns for the common real-world case (single-way rings).
    OSM technically allows a multipolygon ring to be split across several
    *open* way segments that must be chained end-to-end before closing --
    that reassembly is NOT implemented here; such relations are skipped
    and counted separately (relations_skipped), never silently dropped
    into the "built" count.

    Building via Part.Face([outer_wire, *hole_wires]) directly triggered
    "Bad orientation of sub-shape" OCC errors on real-world rings in
    testing (self-consistent per-ring winding doesn't guarantee the
    combined-face convention OCC wants); the robust approach used here
    instead extrudes the outer footprint and each hole footprint
    independently to solids, then boolean-cuts the holes out -- verified
    against real Overpass data (Palais du Louvre, 6 inner courtyards, and
    the Carrousel du Louvre's two-outer-ring multipolygon).
    """
    tags = element.get("tags", {})
    members = element.get("members", [])
    outer_wires = []
    inner_wires = []
    for member in members:
        if member.get("type") != "way" or not member.get("geometry"):
            continue
        wire = _ring_to_wire(Part, FreeCAD, member["geometry"], lat0, lon0)
        if wire is None:
            continue
        if member.get("role") == "outer":
            outer_wires.append(wire)
        elif member.get("role") == "inner":
            inner_wires.append(wire)

    if not outer_wires:
        return []

    height_m, source = parse_height_m(tags)
    height_mm = height_m * 1000.0

    inner_faces = []
    for iw in inner_wires:
        try:
            iface = Part.Face(iw)
            if iface.isValid():
                inner_faces.append(iface)
        except Exception:  # noqa: BLE001
            continue

    solids = []
    for ow in outer_wires:
        try:
            outer_face = Part.Face(ow)
        except Exception:  # noqa: BLE001
            continue
        if not outer_face.isValid():
            continue
        solid = outer_face.extrude(FreeCAD.Vector(0, 0, height_mm))
        for iface in inner_faces:
            try:
                if not outer_face.isInside(iface.CenterOfMass, 1e-6, True):
                    continue
                hole_solid = iface.extrude(FreeCAD.Vector(0, 0, height_mm * 1.2 + 10.0))
                hole_solid.Placement.Base = FreeCAD.Vector(
                    0, 0, -(height_mm * 0.1 + 5.0)
                )
                cut = solid.cut(hole_solid)
                if cut.isValid() and cut.Volume > 0:
                    solid = cut
            except Exception:  # noqa: BLE001 - hole failed, keep solid without it
                continue
        if solid.isValid() and solid.Volume > 0:
            solids.append(solid)
    return [(s, height_m, source) for s in solids]


def build_site(
    FreeCAD,
    Part,
    Mesh,
    doc_name,
    bbox,
    label,
    osm_data,
    elevation_grid=None,
    elevation_sample_points=None,
    progress_cb=None,
    progress_every=15,
    include_buildings=True,
    imagery=None,
    ground_plane=True,
):
    """Build a FreeCAD document from fetched OSM data (+ optional elevation
    grid) for one Add-Location request. Returns (doc, stats).

    progress_cb(current, total, phase), if given, is called periodically
    during the building loop (every progress_every features) so a GUI
    caller can pump QApplication.processEvents() and update a "building
    N/M" label without moving this FreeCAD-API-heavy work off the main
    thread (unsafe -- see module docstring).

    include_buildings=False skips the whole Overpass way/relation loop
    (2D map mode with the buildings checkbox off); counts stay zero and no
    bldg_* objects are created even if osm_data carries elements.

    imagery, when given, is an imagery.fetch_mosaic() result dict; the
    stitched image is placed as a flat Image::ImagePlane sized to its exact
    lat/lon bounds. ground_plane=False suppresses the flat ground box (2D
    map mode, where the imagery plane is the base). Defaults reproduce the
    v0.2 3D-site behavior exactly.
    """
    s, w, n, e = bbox
    lat0 = (s + n) / 2.0
    lon0 = (w + e) / 2.0

    doc = FreeCAD.newDocument(doc_name)
    group = doc.addObject("App::DocumentObjectGroup", "SiteContext")
    group.Label = "SiteContext"

    if include_buildings:
        elements = osm_data.get("elements", [])
        ways = [
            el for el in elements if el.get("type") == "way" and "building" in el.get("tags", {})
        ]
        relations = [
            el
            for el in elements
            if el.get("type") == "relation" and "building" in el.get("tags", {})
        ]
    else:
        ways = []
        relations = []

    total = len(ways) + len(relations)
    fetched = len(ways)
    built = 0
    skipped = 0
    relations_fetched = len(relations)
    relations_built = 0
    relations_skipped = 0
    height_sources = {"height": 0, "levels": 0, "default": 0}

    def _tick(i):
        if progress_cb and (i % progress_every == 0 or i == total):
            progress_cb(i, total, "building")

    i = 0
    for element in ways:
        i += 1
        try:
            result = _build_way_solid(Part, FreeCAD, element, lat0, lon0)
        except Exception:  # noqa: BLE001 - any OCC/geometry failure -> skip
            result = None
        if result is None:
            skipped += 1
            _tick(i)
            continue
        solid, height_m, source = result
        height_sources[source] += 1
        way_id = element.get("id", built)
        tags = element.get("tags", {})
        name = tags.get("name") or tags.get("building") or "building"
        obj = doc.addObject("Part::Feature", f"bldg_{way_id}")
        obj.Label = f"{name}_{way_id}"[:60]
        obj.Shape = solid
        # Part::Feature.Visibility defaults to False headless (no
        # ViewProvider at creation time under freecadcmd) -- force it on.
        obj.Visibility = True
        group.addObject(obj)
        built += 1
        _tick(i)

    for element in relations:
        i += 1
        try:
            results = _build_relation_solids(Part, FreeCAD, element, lat0, lon0)
        except Exception:  # noqa: BLE001
            results = []
        if not results:
            relations_skipped += 1
            _tick(i)
            continue
        tags = element.get("tags", {})
        rel_id = element.get("id", built)
        name = tags.get("name") or tags.get("building") or "building_rel"
        for idx, (solid, height_m, source) in enumerate(results):
            height_sources[source] += 1
            obj = doc.addObject("Part::Feature", f"bldgrel_{rel_id}_{idx}")
            obj.Label = f"{name}_{rel_id}_{idx}"[:60]
            obj.Shape = solid
            obj.Visibility = True
            group.addObject(obj)
        relations_built += 1
        _tick(i)

    # --- Ground / terrain -------------------------------------------------
    gx0, gy0 = project_latlon(s, w, lat0, lon0)
    gx1, gy1 = project_latlon(n, e, lat0, lon0)
    ground_w_mm = abs(gx1 - gx0) * 1000.0
    ground_d_mm = abs(gy1 - gy0) * 1000.0

    terrain_stats = {"attempted": False, "used": False, "relief_m": None, "reason": None}
    if elevation_grid is not None:
        terrain_stats["attempted"] = True
        r = terrain_relief(elevation_grid)
        terrain_stats["relief_m"] = r
        if r > RELIEF_THRESHOLD_M:
            grid_n = len(elevation_grid)
            mid = grid_n // 2
            datum_elev = elevation_grid[mid][mid]
            mesh = build_terrain_mesh(
                Mesh, FreeCAD, elevation_grid, elevation_sample_points, lat0, lon0, datum_elev
            )
            terrain_obj = doc.addObject("Mesh::Feature", "Terrain")
            terrain_obj.Mesh = mesh
            terrain_obj.Visibility = True
            group.addObject(terrain_obj)
            terrain_stats["used"] = True
            terrain_stats["reason"] = (
                f"relief {r:.1f}m exceeds {RELIEF_THRESHOLD_M}m threshold; "
                "buildings remain flat-based at the datum elevation "
                "(local ground elevation is NOT draped under each building "
                "individually -- documented simplification, see README)."
            )
        else:
            terrain_stats["reason"] = (
                f"relief {r:.1f}m at/below {RELIEF_THRESHOLD_M}m threshold; "
                "flat ground plane used."
            )

    if not terrain_stats["used"] and ground_plane:
        ground = doc.addObject("Part::Box", "GroundPlane")
        ground.Length = ground_w_mm
        ground.Width = ground_d_mm
        ground.Height = GROUND_THICKNESS_M * 1000.0
        ground.Placement.Base = FreeCAD.Vector(
            min(gx0, gx1) * 1000.0, min(gy0, gy1) * 1000.0, -GROUND_THICKNESS_M * 1000.0
        )
        ground.Visibility = True
        if getattr(ground, "ViewObject", None):
            ground.ViewObject.ShapeColor = (0.55, 0.62, 0.45)
        group.addObject(ground)
        if elevation_grid is None:
            terrain_stats["reason"] = "no elevation data requested/available; flat ground plane used."

    # --- Imagery plane (2D map mode) ----------------------------------------
    imagery_stats = None
    if imagery is not None:
        imagery_stats = _add_imagery_plane(doc, group, FreeCAD, imagery, lat0, lon0)

    # --- Georeference (document + group properties) -----------------------
    _add_georef_properties(
        group, lat0, lon0, bbox,
        include_buildings=include_buildings,
        imagery_attribution=(
            imagery["attribution"] if imagery_stats is not None else None
        ),
    )

    attribution_lines = []
    if include_buildings:
        attribution_lines.append(OSM_ATTRIBUTION)
    if elevation_grid is not None:
        attribution_lines.append(ELEVATION_ATTRIBUTION)
    if imagery_stats is not None:
        attribution_lines.append(imagery["attribution"])
    doc.Comment = (
        f"SiteContext -- {label}\n"
        f"bbox (S,W,N,E) = ({s},{w},{n},{e})\n"
        f"Origin (lat,lon) = ({lat0:.6f},{lon0:.6f})\n"
        f"{PROJECTION_NOTE}\n" + "\n".join(attribution_lines)
    )

    doc.recompute()
    _force_gui_visibility(FreeCAD, group)

    stats = {
        "label": label,
        "bbox": bbox,
        "fetched": fetched,
        "built": built,
        "skipped": skipped,
        "relations_fetched": relations_fetched,
        "relations_built": relations_built,
        "relations_skipped": relations_skipped,
        "height_sources": height_sources,
        "ground_w_m": ground_w_mm / 1000.0,
        "ground_d_m": ground_d_mm / 1000.0,
        "terrain": terrain_stats,
        "include_buildings": include_buildings,
        "imagery": imagery_stats,
    }
    return doc, stats


def _force_gui_visibility(FreeCAD, group):
    """Force the GUI visibility flags for the group and all its members.

    Setting App-side ``Visibility`` does not drive the GUI view provider:
    a document built or saved in a console session can load with every
    object hidden (observed 2026-07-18: a freecadcmd-saved smoke document
    opened in the GUI with both the group and the imagery plane at
    ViewObject.Visibility == False). When the GUI is up, set the view flags
    explicitly. A no-op under freecadcmd (``App.GuiUp`` is False there).
    """
    if not getattr(FreeCAD, "GuiUp", False):
        return
    for obj in [group] + list(getattr(group, "Group", [])):
        vo = getattr(obj, "ViewObject", None)
        if vo is not None:
            vo.Visibility = True


def _add_imagery_plane(doc, group, FreeCAD, imagery, lat0, lon0):
    """Place the stitched map/satellite image as a flat Image::ImagePlane
    sized to its exact lat/lon bounds (imagery.fetch_mosaic() result dict).
    The quad is centered on Placement.Base and displays north-up -- see
    imagery.py's module docstring for the verified ImagePlane contract.
    Returns the stats dict recorded in stats["imagery"]."""
    from .imagery import plane_metrics

    try:
        import Image  # noqa: F401 - registers the Image::ImagePlane type
    except Exception:  # noqa: BLE001 - already registered, or GUI-side
        pass

    x_size, y_size, cx, cy, z = plane_metrics(imagery["bounds_latlon"], lat0, lon0)
    plane = doc.addObject("Image::ImagePlane", "SiteImagery")
    plane.Label = "Imagery ({}, z{})".format(imagery["provider_label"], imagery["zoom"])
    plane.ImageFile = imagery["image_path"]
    plane.XSize = x_size
    plane.YSize = y_size
    plane.Placement.Base = FreeCAD.Vector(cx, cy, z)
    plane.Visibility = True
    plane.addProperty(
        "App::PropertyString", "Attribution", "SiteContext Imagery",
        "Imagery attribution (provider terms require it)",
    )
    plane.Attribution = imagery["attribution"]
    plane.addProperty(
        "App::PropertyString", "Provider", "SiteContext Imagery",
        "Imagery provider and zoom",
    )
    plane.Provider = "{} zoom {}".format(imagery["provider_key"], imagery["zoom"])
    group.addObject(plane)

    return {
        "provider_key": imagery["provider_key"],
        "provider_label": imagery["provider_label"],
        "zoom": imagery["zoom"],
        "tiles_fetched": imagery["tiles_fetched"],
        "tiles_failed": imagery["tiles_failed"],
        "cached": imagery["cached"],
        "image_path": imagery["image_path"],
        "width_px": imagery["width_px"],
        "height_px": imagery["height_px"],
        "width_m": x_size / 1000.0,
        "height_m": y_size / 1000.0,
        "attribution": imagery["attribution"],
    }


def _add_georef_properties(group, lat0, lon0, bbox, include_buildings=True, imagery_attribution=None):
    """Store the geo origin as document(-group) properties so the site is
    georeferenced-in-spirit, per the task brief: local origin lat/lon, the
    equirectangular-approximation caveat, and that meters are exact at the
    origin. Not a substitute for a real CRS/EPSG-tagged export.
    """
    s, w, n, e = bbox
    props = [
        ("App::PropertyFloat", "OriginLatitude", lat0, "Latitude of the local projection origin (deg, WGS84)"),
        ("App::PropertyFloat", "OriginLongitude", lon0, "Longitude of the local projection origin (deg, WGS84)"),
        ("App::PropertyString", "ProjectionNote", PROJECTION_NOTE, "Projection accuracy caveat"),
        (
            "App::PropertyString",
            "SourceBBox",
            f"S={s} W={w} N={n} E={e}",
            "Source Overpass bbox (WGS84 degrees)",
        ),
    ]
    if include_buildings:
        props.append(
            ("App::PropertyString", "Attribution", OSM_ATTRIBUTION, "Data attribution (ODbL)")
        )
    if imagery_attribution:
        props.append(
            ("App::PropertyString", "ImageryAttribution", imagery_attribution,
             "Imagery attribution (provider terms require it)")
        )
    for prop_type, name, value, doc_str in props:
        if name not in group.PropertiesList:
            group.addProperty(prop_type, name, "SiteContext Georeference", doc_str)
        setattr(group, name, value)
