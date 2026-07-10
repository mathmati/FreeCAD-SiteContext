# SPDX-License-Identifier: MIT
"""SiteContext workbench package (Modern namespaced layout).

Importing this package has no side effects beyond making the submodules
importable -- workbench/command registration happens in init_gui.py, which
is imported once by the top-level InitGui.py shim when FreeCAD's Addon
Manager (or a Mod/ install) loads this addon. Per FreeCAD's Qualities
checklist, nothing here does network access or expensive work at import
time.
"""
