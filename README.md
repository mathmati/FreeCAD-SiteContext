# FreeCAD SiteContext — "Add Location…"

A FreeCAD 1.1 workbench that gives FreeCAD the **SketchUp "Add Location" /
BlenderGIS** experience, built entirely on open data: pick a place (by
lat/lon, or by name via geocoding) and it fetches real OpenStreetMap
building footprints and coarse terrain elevation around it, then builds a
3D site-context model — grouped under a `SiteContext` object — ready to
design against.

This is v0.2, the first real addon build, evolved from a headless
prototype that
proved the fetch → project → extrude → assemble pipeline end to end.

## Heritage & prior art

- **SketchUp's "Add Location"** — the direct inspiration: pick a spot on a
  map, get real terrain + imagery draped under your model, so you're
  designing in context instead of a blank void.
- **BlenderGIS** — proved the same idea works well on fully open data
  (OSM buildings, open elevation APIs) without a proprietary imagery
  license.
- **microelly2/geodata ("GeoData workbench")** — the closest FreeCAD-native
  ancestor of this idea: `Import OSM Map` (lat/lon or a pasted map link)
  pulled roads and building **ways** into the document, with separate
  `Import OSM Heights`/`Import SRTM Heights` commands adding elevation as a
  raw point cloud. It was removed from the FreeCAD Addon Manager around
  November 2020 (its own issue #21) and the FreeCAD forum describes it as
  "started... but never completed... more or less prototype code." We
  credit it as the original idea for OSM-into-FreeCAD.
- **rostskadat/FreeCAD-geodata2 ("GeoData2")** — an explicit fork/rework of
  microelly2/geodata, listed on the FreeCAD wiki as "an updated version of
  GeoData featuring preferences and user interface enhancements" and still
  available via the Addon Manager today. Its last commit is 2024-09-30
  (`package.xml` declares a minimum FreeCAD version of 0.20.0, predating
  FreeCAD 1.0), and it inherits the same scope as the original: ways-only
  building import (no relation/multipolygon support), raw point-cloud
  heights (no generated terrain mesh), and no place-name geocoding — with
  no evidence found of anyone verifying it against FreeCAD 1.0 or 1.1.

**Honest rewrite rationale:** rather than patch either predecessor, this
addon is a fresh build, because the hardest half of what SiteContext does
— OSM building **relations**/multipolygons with hole geometry, a generated
terrain **mesh** with a flat-plane fallback, Nominatim place-name search,
and documented attribution/politeness handling — is missing from both, and
both carry an older `geodat_`-style architecture with no confirmed FreeCAD
1.x verification. SiteContext is positioned as the actively-maintained,
FreeCAD-1.x-verified evolution of that lineage, not as a "first of its
kind" — it follows the same idea as SketchUp/BlenderGIS and the GeoData
lineage for FreeCAD, using only free/open data sources (OpenStreetMap +
public elevation/geocoding APIs), with an honest accounting of what's
approximated (see "Accuracy limits" below). A courtesy note may be filed
on rostskadat/FreeCAD-geodata2 to disclose this addon's existence before
any Index submission.

## What it does

1. **Add Location… dialog** (Site Context workbench toolbar/menu):
   - **Coordinates tab**: latitude, longitude, and a radius (100–500m).
   - **Place name tab**: free-text search via Nominatim (OpenStreetMap's
     public geocoder), with a results list to pick the right match.
   - A **preset dropdown** with 3 example locations (see below).
   - **Fetch & Build**: runs the network fetch on a background thread
     (so the UI doesn't freeze) with live status text ("fetching OSM
     buildings...", "sampling terrain elevation 100/225..."), then builds
     the FreeCAD geometry on the main thread with a "building N/M..."
     progress bar (FreeCAD's document/geometry API is not thread-safe, so
     this half pumps `QApplication.processEvents()` instead of moving to
     a worker thread — see `add_location_dialog.py`/`site_builder.py`).
2. **Buildings**: every OSM building **way** *and*, new in v0.2, every
   building **relation** (multipolygon — courtyards, complex campuses)
   in the fetch area becomes an extruded solid. Height priority: `height`
   tag → `building:levels` × 3m/level → an 8m default. Relations with
   holes (inner rings) get the hole geometry boolean-cut out, not just
   the outer footprint extruded — verified against real-world data (the
   Palais du Louvre's courtyards, the Carrousel du Louvre's two-outer-ring
   multipolygon).
3. **Terrain v1**: samples a coarse 15×15 grid of elevation points from
   the public `api.opentopodata.org` SRTM-90m dataset. If the relief
   across the area exceeds ~2m, a terrain surface (a `Mesh::Feature`
   heightfield) is generated and used as the ground instead of a flat
   plane. If the area is flat or the API is unavailable, it falls back
   to a flat ground plane sized to the fetch area — gracefully, not as
   an error.
4. **Attribution & georeference**: the OSM ODbL attribution string is
   written into the document's `Comment` and the dialog's footer; the
   `SiteContext` group object carries `OriginLatitude`/`OriginLongitude`
   custom properties (the projection origin) plus a `ProjectionNote` and
   `Attribution` string, so the model is georeferenced-in-spirit even
   though it isn't tagged with a formal CRS/EPSG code.

## The 3 presets

| Preset | Why it's here |
|---|---|
| Trafalgar Square, London | Dense mixed-use baseline — the v0 prototype's original case, kept as the known-good regression. |
| Palais du Louvre, Paris | OSM building **relations**: the Palais itself (6 courtyard holes) and the Carrousel du Louvre (a 2-outer-ring multipolygon) — the v0.2 regression case for relation support (v0 silently skipped every relation). |
| Russian Hill / Lombard St, San Francisco | Real elevation relief (tens of meters per SRTM 90m over a few hundred meters) — exercises the terrain-mesh path. |

## Install

### Manually (developer / local test install)

Copy (not symlink, to reproduce exactly what the Addon Manager does) this
folder into your FreeCAD `Mod/` directory:

- Linux: `~/.local/share/FreeCAD/Mod/` (or `~/.local/share/FreeCAD/v1-1/Mod/`
  on some 1.1 installs)
- Windows: `%APPDATA%\FreeCAD\Mod\`
- macOS: `~/Library/Application Support/FreeCAD/Mod/`

Restart FreeCAD, check **View → Workbenches → Site Context**, and use
**Add Location…** from its toolbar/menu.

### Via the Addon Manager (once indexed / added as a custom repository)

Once published, add this repository's URL under **Addon Manager →
Configure → Custom repositories** for early access before indexing, or
search "SiteContext" after it's indexed.

## Data sources, licenses, and politeness policies

- **Buildings**: [OpenStreetMap](https://www.openstreetmap.org/copyright)
  contributors, via the public [Overpass API](https://overpass-api.de/)
  (with `overpass.kumi.systems` as a fallback mirror). Licensed
  **ODbL 1.0** — any redistribution of generated models must keep the
  attribution and comply with ODbL's share-alike terms for the underlying
  map data. Every fetch is a small bbox (radius ≤ 500m) with a descriptive
  User-Agent, and is cached locally (per addon cache dir) so a given area
  is only ever fetched once per session.
- **Geocoding**: [Nominatim](https://nominatim.openstreetmap.org/), per
  its [usage policy](https://operations.osmfoundation.org/policies/nominatim/):
  a descriptive User-Agent, a minimum 1-second gap between requests
  (enforced defensively in `geocode.py` even though the dialog only ever
  issues one request per user-initiated "Search" click), results capped
  at 5, no bulk/systematic queries, no results persisted to disk.
- **Terrain elevation**: [api.opentopodata.org](https://www.opentopodata.org/)
  SRTM-90m dataset, a free public service. Its docs ask for ≤100
  locations/request and roughly 1 request/second; `terrain.py` batches
  the 225-point sample grid into ≤100-point requests with a sleep between
  them.
- No API keys, no accounts, no telemetry. The only outbound network calls
  this addon ever makes are the three above, and only when the user clicks
  Search or Fetch & Build — never at import/startup time.

## Accuracy limits (read this before trusting the model for anything real)

- **Projection**: lat/lon → local meters uses a simple equirectangular
  (plate-carrée) approximation around the fetch area's center — NOT a
  conformal or equal-area projection. Meters are exact *at* the origin;
  distortion grows with distance from it and with absolute latitude. This
  is fine at the addon's supported scale (radius ≤ 500m, temperate
  latitudes) and NOT acceptable at kilometer scale or near the poles. A
  future version should use a proper local projection (UTM zone via
  `pyproj`, or FreeCAD's own georeferencing/Coordinates tooling).
- **Relation ring assembly**: this v0.2 assumes each multipolygon
  outer/inner *member way* is already an individually-closed ring, which
  is the common real-world case (and what was verified against the Louvre
  test data). OSM technically also allows a ring to be split across
  several open way segments that must be chained end-to-end before
  closing; that reassembly is **not** implemented — such relations are
  skipped and counted (`relations_skipped`), never silently dropped into
  the built count.
- **Terrain is a heightfield, not draped under buildings.** When a
  terrain surface is generated, buildings still sit flat-based at one
  datum elevation (the grid's center-point sample) — they are not
  individually re-based to their own local ground height. This is a
  documented v1 simplification, not a bug; buildings on a sloped block
  will visually float or sink slightly relative to the terrain mesh at
  their footprint's actual corners.
- **SRTM 90m over dense urban cores reflects rooftops, not bare earth.**
  In testing, even flat city centers (Trafalgar Square, the Louvre)
  showed 14–26m of "relief" — almost certainly building-height noise in
  the DEM at 90m resolution, not real ground slope. The ≥2m threshold
  therefore triggers the terrain-mesh path more often than a bare-earth
  DEM would; treat the generated terrain as "there's *some* elevation
  signal here" context, not a surveyed ground model. The Russian
  Hill/Lombard St preset is the one location in this set with elevation
  relief that's plausibly real hill topography, not just DEM noise.
- **No roof shapes.** All buildings are flat-topped extrusions; OSM's
  `roof:shape`/`roof:height` tags are not yet used.
- **No imagery drape.** The ground/terrain surface has no aerial/satellite
  texture (SketchUp/BlenderGIS's other headline feature) — see Roadmap.
- **Overpass/opentopodata size limits**: this addon does not tile large
  areas into multiple queries; very large radii near the 500m cap in
  dense areas may approach Overpass's practical timeout.

## Roadmap (not in this version)

- Imagery drape on the ground/terrain surface (an open aerial/satellite
  tile source).
- Proper local projection (UTM/`pyproj`) instead of the equirectangular
  approximation, for exports that interoperate with GIS tools (QGIS etc).
- Roof shapes from `roof:shape`/`roof:height` tags.
- Per-building local ground elevation (draping each footprint onto the
  terrain surface, not just a single datum).
- Reassembling multi-way multipolygon rings that aren't individually
  closed.
- Tiling large areas of interest into multiple Overpass queries.
- i18n: UI strings are not yet wrapped in `FreeCAD.Qt.translate`/
  `QT_TRANSLATE_NOOP` — a known gap for the Qualities checklist's i18n
  guidance, scoped for the next pass before an Addon Index submission.

## Privacy / compliance

No telemetry, no accounts, no data sent anywhere except the three
documented API calls (Overpass, Nominatim, opentopodata), each only on
explicit user action (Search / Fetch & Build). Cached Overpass responses
are stored locally under this addon's own FreeCAD user-data cache
directory and never uploaded anywhere.

## License

Code is MIT-licensed — see [`LICENSE`](LICENSE). The manifest
(`package.xml`) declares the same SPDX identifier consistently. The icon
(`Resources/Icons/sitecontext.svg`) is original artwork under the same
MIT license. Generated site models are NOT covered by this license — they
embed OpenStreetMap data and remain subject to ODbL 1.0 (see above).

## Contributing

Issues and pull requests are welcome once this repository is public. Please:
- Keep new UI strings translation-ready (`FreeCAD.Qt.translate`) going
  forward.
- Keep icons SVG-only; never commit compiled Qt resources (`.rcc`).
- Disclose any AI assistance in your PR description and with an
- Respect the politeness policies in "Data sources" above for any new API
  integration.
