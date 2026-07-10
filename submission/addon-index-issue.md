PREPARED DRAFT — awaiting user review and sign-off before any external submission.

---

**Where this goes, when approved:** an issue of type "Addon - Addition" at
`https://github.com/FreeCAD/Addons/issues/new/choose` (or the Codeberg
mirror `https://codeberg.org/FreeCAD/Addons`), per the Addon-Academy
publishing guide.

**Before this can actually be filed:** the addon needs its own public repo
(currently it lives inside a private FableMax monorepo), a tagged release
on a stable `main`/`release` branch, a multi-platform (Windows/macOS) Mod
path sanity check (only Linux was exercised this session — see
`submission/DISCLOSURE.md`), and every `[PLACEHOLDER]` below filled in.

**Prior-art search performed:** a dedicated prior-art search (live
WebSearch/WebFetch only, no git commands, no sub-agents) was performed
before this addon was considered release-quality — see
`ops/novelty-sitecontext.md` (2026-07-10) for the full method and
evidence.

---

## Issue title

Addon Addition: FreeCAD SiteContext

## Repository

`https://github.com/mathmati/FreeCAD-SiteContext`
(branch: `main`)

## Description

**FreeCAD SiteContext** gives FreeCAD the SketchUp "Add Location" /
BlenderGIS experience on fully open data: pick a place (by lat/lon, or by
name via Nominatim geocoding) and it fetches real OpenStreetMap building
footprints and coarse SRTM-90m terrain elevation around it, then builds a
3D site-context model — grouped under a `SiteContext` object — ready to
design against. Every building **way** and, new in this version, every
building **relation** (multipolygon, e.g. courtyards) becomes an extruded
solid with holes boolean-cut where applicable; a terrain surface (mesh
heightfield) is generated when relief exceeds a small threshold, falling
back gracefully to a flat ground plane otherwise. No API keys, no
accounts, no telemetry — the only outbound calls are Overpass, Nominatim,
and opentopodata, each only on explicit user action.

## Heritage and prior art

This is not a "first of its kind" addon — it succeeds a real FreeCAD
lineage, disclosed here rather than left for a reviewer to find:

- **microelly2/geodata** ("GeoData workbench") — the original FreeCAD
  OSM-import addon. Removed from the FreeCAD Addon Manager around
  November 2020 (its own issue #21); the FreeCAD forum describes it as
  "started... but never completed... more or less prototype code." Ways
  only, no relations, raw point-cloud heights, no FreeCAD 1.x
  verification (predates it by years).
- **rostskadat/FreeCAD-geodata2** ("GeoData2") — an explicit fork/rework
  of the above, still listed in the Addon Manager today ("an updated
  version of GeoData featuring preferences and user interface
  enhancements" per the FreeCAD wiki). Last commit 2024-09-30;
  `package.xml` declares a minimum FreeCAD version of 0.20.0 (predating
  FreeCAD 1.0's release), and no forum/GitHub evidence was found of
  anyone verifying it against FreeCAD 1.0 or 1.1. Same scope as the
  original: ways-only, no relation/multipolygon support, no generated
  terrain mesh, no place-name geocoding.

SiteContext is a fresh build rather than a patch to either, because the
harder half of what it does — relations/multipolygons with holes, a
generated terrain mesh with fallback logic, Nominatim geocoding, and
documented attribution/politeness policies — is missing from both, and
both carry an older architecture with no confirmed 1.x verification. We
position this addon as the actively-maintained, FreeCAD-1.x-verified
evolution of that lineage, crediting both predecessors by name — not as
competing with them silently. A courtesy note is planned for
rostskadat/FreeCAD-geodata2 disclosing this addon's existence before or
alongside this Index submission (see `ops/novelty-sitecontext.md`
recommendation 3).

## How this addon meets the Qualities checklist

- **Governance:** named maintainer, `mathmati`
  (`177616452+mathmati@users.noreply.github.com`), actively watching issues/PRs on the
  repository above.
- **Compliance:** makes network calls only to Overpass, Nominatim, and
  opentopodata, only on explicit user action (Search / Fetch & Build),
  never at import/startup time; documented politeness policies (User-Agent,
  rate limiting, result caps, local caching) for each — see the README's
  "Data sources, licenses, and politeness policies" section.
- **Licensing:** code MIT (`package.xml` `<license>` + `LICENSE-Code`).
  Generated site models embed OpenStreetMap data and remain subject to
  ODbL 1.0 — disclosed plainly in the README, not silently inherited.
- **Codebase:**
  - Modern namespaced layout (`freecad/SiteContextWB/`), no legacy
    `InitGui.py` shim, no `sys.path` manipulation.
  - Uses FreeCAD's provided Qt wrapper exclusively.
  - `<classname>SiteContextWorkbench</classname>` matches the registered
    `Gui.Workbench` subclass exactly; `GetClassName()` returns
    `"Gui::PythonWorkbench"`.
  - Network fetch runs on a background thread so the UI doesn't freeze;
    geometry build runs on the main thread (FreeCAD's document/geometry
    API is not thread-safe), disclosed in the README rather than hidden.
  - SVG-only icon (`Resources/Icons/sitecontext.svg`); no compiled Qt
    resources.
- **Best practices:** version/date (`0.2.0` / `2026-07-10`) will be
  bumped on every change reaching the release branch; repository will
  carry the `freecad` and `addon` topics.

**Known gaps, disclosed up front rather than discovered in review:**
equirectangular (not conformal/equal-area) projection, acceptable only at
the addon's supported scale (radius ≤500m, temperate latitudes); no
imagery drape; no roof shapes; terrain is a single-datum heightfield, not
per-building draped; SRTM-90m over dense urban cores likely reflects
rooftop noise rather than bare earth (documented in the README's
"Accuracy limits" section); UI strings are not yet translation-wrapped.
None of these are hidden — see the README's "Accuracy limits" and
"Roadmap" sections for the full, honest list.

## AI-assistance disclosure

This addon was built with the assistance of an AI coding assistant
(Claude, by Anthropic), reviewed, tested, and taken responsibility for by
the named human maintainer above, per FreeCAD's `AI_POLICY.md`. Full
disclosure text and the `Assisted-by:` trailer convention used on this
project's commits are in the repository's `DISCLOSURE.md` (mirrors
`submission/DISCLOSURE.md` in the build record), including the heritage/
prior-art disclosure above. All review responses on this issue and any
follow-up PRs will be written personally by the maintainer, not relayed
AI output.

## Beta-testing note

Per the publishing guide's recommendation to beta-test with real users
before requesting indexing: [PLACEHOLDER — describe where this was
shared for real-user feedback, e.g. a FreeCAD forum GIS/BIM subforum
thread URL, or state that beta-testing is still pending and this issue
is filed as a work-in-progress/PoC per the guide's allowance for
clearly-marked WIP addons].
