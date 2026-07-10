# SPDX-License-Identifier: MIT
"""Preset example locations for the Add Location dialog.

Chosen to exercise the three headline v0.2 improvements over the v0
prototype in one small set:

- Trafalgar Square: dense mixed-use central London, plain `way` footprints
  (the v0 prototype's original case, kept as the "known good" baseline).
- Palais du Louvre: OSM `relation` multipolygon buildings -- the Louvre
  Palace itself has a courtyard modeled as inner-ring holes, and the
  Carrousel du Louvre is a multi-outer-ring multipolygon. v0 silently
  skipped ALL relations; this is the regression case for that fix.
- Russian Hill / Lombard Street, San Francisco: real elevation relief
  (~50m across a few hundred meters per SRTM 90m), to exercise the new
  terrain sampling/generation path. Flat cities like the other two
  presets are expected to fall back to a flat ground plane.
"""

PRESETS = [
    {
        "key": "trafalgar_square",
        "label": "Trafalgar Square, London (dense mixed-use)",
        "lat": 51.5077,
        "lon": -0.1281,
        "radius_m": 180,
    },
    {
        "key": "louvre_paris",
        "label": "Palais du Louvre, Paris (relation/multipolygon buildings)",
        "lat": 48.8610,
        "lon": 2.3360,
        "radius_m": 300,
    },
    {
        "key": "russian_hill_sf",
        "label": "Russian Hill / Lombard St, San Francisco (terrain relief)",
        "lat": 37.8021,
        "lon": -122.4187,
        "radius_m": 250,
    },
]
