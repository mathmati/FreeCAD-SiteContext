# SPDX-License-Identifier: MIT
"""Workbench registration for the SiteContext addon.

Importing this module (auto-discovered by FreeCAD's Modern-layout addon
loader from Mod/<addon>/freecad/SiteContextWB/) registers the workbench
with Gui.addWorkbench(...). Per the Qualities checklist this does no
network access or other expensive work at import/startup time -- the
"Add Location..." command only fetches on explicit user action (the
dialog's Fetch & Build button).
"""
import os

import FreeCADGui as Gui


class SiteContextWorkbench(Gui.Workbench):
    MenuText = "Site Context"
    ToolTip = "Add real-world OSM site context (buildings + terrain) around a location"
    Icon = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "Resources",
        "Icons",
        "sitecontext.svg",
    )

    def Initialize(self):
        # Import side effect registers the command with Gui.addCommand.
        from . import commands

        commands.register()
        self.appendToolbar("Site Context", ["SiteContext_AddLocation"])
        self.appendMenu("Site Context", ["SiteContext_AddLocation"])

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"  # exact string, mandatory, do not change


Gui.addWorkbench(SiteContextWorkbench())
