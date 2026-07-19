# SPDX-License-Identifier: MIT
"""Dialog output choices: persistence in FreeCAD parameters + fetch planning.

Kept FreeCADGui/PySide-free on purpose so verify/headless_regression.py can
exercise the parameter round-trip and the fetch-plan logic under freecadcmd
without importing the dialog module.
"""
import FreeCAD as App

from . import imagery

PARAM_GROUP = "User parameter:BaseApp/Preferences/Mod/SiteContext"

MODE_2D_MAP = "map2d"
MODE_3D_SITE = "site3d"

DEFAULT_OUTPUT_MODE = MODE_2D_MAP
DEFAULT_INCLUDE_BUILDINGS = False


def _params():
    return App.ParamGet(PARAM_GROUP)


def load_settings():
    """Return the persisted dialog choices: {"output_mode", "include_buildings",
    "imagery_provider"}. First run gets the 2D map default (the field-report
    expectation: the map is the primary output, 3D buildings opt-in)."""
    params = _params()
    mode = params.GetString("OutputMode", DEFAULT_OUTPUT_MODE)
    if mode not in (MODE_2D_MAP, MODE_3D_SITE):
        mode = DEFAULT_OUTPUT_MODE
    provider = params.GetString("ImageryProvider", imagery.DEFAULT_PROVIDER)
    if provider not in imagery.PROVIDERS:
        provider = imagery.DEFAULT_PROVIDER
    # One-time migration for the v0.3 -> v0.4 default flip (Esri -> OSM).
    # Before the flip the saved provider was just "whatever the last fetch
    # used", not an explicit choice, so a pre-flip Esri value would silently
    # pin the old default forever. Reset it once; from now on every save
    # marks the value as chosen, so a deliberate Esri pick sticks.
    if not params.GetBool("ImageryProviderChosen", False):
        provider = imagery.DEFAULT_PROVIDER
    return {
        "output_mode": mode,
        "include_buildings": params.GetBool(
            "IncludeBuildings", DEFAULT_INCLUDE_BUILDINGS
        ),
        "imagery_provider": provider,
    }


def save_settings(output_mode, include_buildings, imagery_provider):
    params = _params()
    params.SetString("OutputMode", output_mode)
    params.SetBool("IncludeBuildings", bool(include_buildings))
    params.SetString("ImageryProvider", imagery_provider)
    params.SetBool("ImageryProviderChosen", True)


def fetch_plan(output_mode, include_buildings, want_terrain):
    """What one Fetch & Build click will fetch, as {"imagery", "buildings",
    "terrain"} booleans. 3D site mode always includes buildings (behavior
    unchanged from v0.2); 2D map mode fetches imagery, includes buildings
    only when the checkbox is ticked, and never samples terrain (the map is
    a flat plane)."""
    is_map = output_mode == MODE_2D_MAP
    return {
        "imagery": is_map,
        "buildings": include_buildings if is_map else True,
        "terrain": (not is_map) and bool(want_terrain),
    }
