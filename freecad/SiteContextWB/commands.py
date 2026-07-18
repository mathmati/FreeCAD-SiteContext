# SPDX-License-Identifier: MIT
"""FreeCAD Gui.Command subclasses for the SiteContext workbench."""
import os

import FreeCADGui as Gui

_ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "Resources",
    "Icons",
)
_ICON_PATH = os.path.join(_ICON_DIR, "sitecontext.svg")


class AddLocationCommand(object):
    """Opens the "Add Location..." dialog: SiteContext's primary command."""

    def GetResources(self):
        return {
            "MenuText": "Add Location...",
            "ToolTip": (
                "Fetch OpenStreetMap buildings (+ terrain) around a place "
                "and build a 3D site model"
            ),
            "Pixmap": _ICON_PATH,
        }

    def IsActive(self):
        return True

    def Activated(self):
        from . import add_location_dialog

        dlg = add_location_dialog.AddLocationDialog(Gui.getMainWindow())
        dlg.exec_()


def register():
    Gui.addCommand("SiteContext_AddLocation", AddLocationCommand())
