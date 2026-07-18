# SPDX-License-Identifier: MIT
"""The "Add Location..." dialog: SiteContext's primary command surface.

Fields for either (a) a lat/lon center + radius (100-500m), or (b) a
place-name geocoded via Nominatim with a small results list, plus a
preset dropdown of 3 example locations. "Fetch & Build" runs the network
fetch on a background QThread (FetchWorker below) and the FreeCAD-API
geometry build on the main thread with QApplication.processEvents()
pumped every few features -- see site_builder.py's module docstring for
why the geometry half can't safely move to a worker thread.

Uses FreeCAD's own Qt wrapper (`from PySide import ...`), never PySide6
directly, per the Addon-Academy Qualities checklist.
"""
import os
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from . import geocode, imagery, overpass_client, presets, settings, site_builder, terrain
from .projection import bbox_from_center_radius

MIN_RADIUS_M = 100
MAX_RADIUS_M = 500
DEFAULT_RADIUS_M = 200

ATTRIBUTION_FOOTER = (
    "Buildings: © OpenStreetMap contributors, ODbL 1.0 "
    "(openstreetmap.org/copyright). Search: Nominatim, usage policy at "
    "operations.osmfoundation.org/policies/nominatim. Terrain: SRTM 90m via "
    "the public api.opentopodata.org service. Imagery (2D map): Esri World "
    "Imagery (Source: Esri, Vantor, Earthstar Geographics, and the GIS User "
    "Community) or OSM standard tiles (cartography CC BY-SA 2.0); at most "
    f"{imagery.MAX_TILES_PER_IMPORT} tiles per import, cached locally. "
    "Please be a polite API "
    "citizen -- this dialog throttles and caches requests for you."
)


def _cache_dir():
    base = App.getUserAppDataDir() if hasattr(App, "getUserAppDataDir") else "."
    path = os.path.join(base, "SiteContextCache")
    os.makedirs(path, exist_ok=True)
    return path


class FetchWorker(QtCore.QThread):
    """Runs the network calls for one Fetch & Build click off the GUI
    thread: imagery tiles, Overpass buildings and opentopodata elevation,
    each only if the fetch plan (settings.fetch_plan) asks for it. Emits
    progress strings, then either finished_ok(dict) or failed(str). Does
    no FreeCAD-API work -- safe to run in a real thread. The PIL stitch in
    imagery.fetch_mosaic() is pure image work, no FreeCAD API, so it can
    happen here too.
    """

    progress = QtCore.Signal(str)
    finished_ok = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, bbox, label, plan, provider_key, parent=None):
        super(FetchWorker, self).__init__(parent)
        self.bbox = bbox
        self.label = label
        self.plan = plan
        self.provider_key = provider_key

    def run(self):
        try:
            s, w, n, e = self.bbox
            result = {"plan": self.plan, "provider_key": self.provider_key}

            imagery_result = None
            if self.plan.get("imagery"):
                imagery_result = imagery.fetch_mosaic(
                    self.provider_key,
                    s, w, n, e,
                    cache_dir=_cache_dir(),
                    progress_cb=self.progress.emit,
                )
            result["imagery"] = imagery_result

            if self.plan.get("buildings"):
                slug = "adhoc_{:.5f}_{:.5f}_{:.5f}_{:.5f}".format(s, w, n, e)
                cache_path = os.path.join(_cache_dir(), slug + ".json")
                self.progress.emit("fetching OSM buildings (ways + relations)...")
                osm_data = overpass_client.fetch_overpass_bbox(
                    s, w, n, e, cache_path=cache_path, progress_cb=self.progress.emit
                )
            else:
                self.progress.emit(
                    "buildings not requested; skipping the Overpass fetch"
                )
                osm_data = {"elements": []}
            result["osm_data"] = osm_data

            elevation_grid = None
            sample_points = None
            if self.plan.get("terrain"):
                try:
                    sample_points = terrain.sample_grid_points(s, w, n, e)
                    elevation_grid = terrain.fetch_elevation_grid(
                        s, w, n, e, progress_cb=self.progress.emit
                    )
                except terrain.TerrainError as exc:
                    self.progress.emit(
                        f"terrain elevation unavailable ({exc}); "
                        "falling back to a flat ground plane"
                    )
            result["elevation_grid"] = elevation_grid
            result["sample_points"] = sample_points

            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the dialog
            self.failed.emit(f"{exc}\n{traceback.format_exc()}")


class AddLocationDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(AddLocationDialog, self).__init__(parent)
        self.setWindowTitle("Add Location… — SiteContext")
        self.resize(520, 560)

        self._worker = None
        self._bbox = None
        self._label = None
        self._map_buildings_choice = settings.DEFAULT_INCLUDE_BUILDINGS
        self.last_document = None

        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Fetch real-world context around a location: a flat 2D map "
            "(satellite or OpenStreetMap tiles), a 3D site model (OSM "
            "building footprints + terrain), or the map with 3D buildings "
            "on top. Everything is grouped under “SiteContext”."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel("Preset:"))
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.addItem("(custom)")
        for p in presets.PRESETS:
            self.preset_combo.addItem(p["label"])
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo, 1)
        layout.addLayout(preset_row)

        output_box = QtWidgets.QGroupBox("Output")
        output_layout = QtWidgets.QVBoxLayout(output_box)
        mode_row = QtWidgets.QHBoxLayout()
        self.mode_map_radio = QtWidgets.QRadioButton("2D map (satellite)")
        self.mode_site_radio = QtWidgets.QRadioButton("3D site (buildings + terrain)")
        self.mode_map_radio.setChecked(True)
        self.mode_map_radio.toggled.connect(self._on_mode_changed)
        self.mode_site_radio.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_map_radio)
        mode_row.addWidget(self.mode_site_radio)
        mode_row.addStretch(1)
        output_layout.addLayout(mode_row)
        opts_row = QtWidgets.QHBoxLayout()
        opts_row.addWidget(QtWidgets.QLabel("Imagery source:"))
        self.provider_combo = QtWidgets.QComboBox()
        for key, provider in imagery.PROVIDERS.items():
            self.provider_combo.addItem(provider["label"], key)
        self.provider_combo.setToolTip(
            "Tile source for the 2D map. At most "
            f"{imagery.MAX_TILES_PER_IMPORT} tiles per import; the zoom is "
            "chosen automatically to fit. Stitched images are cached locally."
        )
        opts_row.addWidget(self.provider_combo, 1)
        self.buildings_checkbox = QtWidgets.QCheckBox("Include 3D buildings")
        self.buildings_checkbox.setToolTip(
            "Also fetch OpenStreetMap building footprints and extrude them "
            "on top of the map."
        )
        self.buildings_checkbox.toggled.connect(self._on_buildings_toggled)
        opts_row.addWidget(self.buildings_checkbox)
        output_layout.addLayout(opts_row)
        layout.addWidget(output_box)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_coords_tab(), "Coordinates")
        self.tabs.addTab(self._build_place_tab(), "Place name")
        layout.addWidget(self.tabs)

        radius_row = QtWidgets.QHBoxLayout()
        radius_row.addWidget(QtWidgets.QLabel("Radius (m):"))
        self.radius_spin = QtWidgets.QSpinBox()
        self.radius_spin.setRange(MIN_RADIUS_M, MAX_RADIUS_M)
        self.radius_spin.setSingleStep(25)
        self.radius_spin.setValue(DEFAULT_RADIUS_M)
        radius_row.addWidget(self.radius_spin)
        radius_row.addStretch(1)
        self.terrain_checkbox = QtWidgets.QCheckBox("Sample terrain elevation")
        self.terrain_checkbox.setChecked(True)
        self.terrain_checkbox.setToolTip(
            "Coarse 15x15 grid via api.opentopodata.org (SRTM 90m). If relief "
            "exceeds ~2m, a terrain surface is generated instead of a flat "
            "ground plane."
        )
        radius_row.addWidget(self.terrain_checkbox)
        layout.addLayout(radius_row)

        self.build_button = QtWidgets.QPushButton("Fetch && Build")
        self.build_button.clicked.connect(self._on_fetch_and_build)
        layout.addWidget(self.build_button)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setFixedHeight(140)
        layout.addWidget(self.log)

        footer = QtWidgets.QLabel(ATTRIBUTION_FOOTER)
        footer.setWordWrap(True)
        footer.setStyleSheet("color: palette(mid); font-size: 10px;")
        layout.addWidget(footer)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        self.close_button = QtWidgets.QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

    def _build_coords_tab(self):
        widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(widget)

        self.lat_spin = QtWidgets.QDoubleSpinBox()
        self.lat_spin.setRange(-90.0, 90.0)
        self.lat_spin.setDecimals(6)
        self.lat_spin.setValue(presets.PRESETS[0]["lat"])
        form.addRow("Latitude:", self.lat_spin)

        self.lon_spin = QtWidgets.QDoubleSpinBox()
        self.lon_spin.setRange(-180.0, 180.0)
        self.lon_spin.setDecimals(6)
        self.lon_spin.setValue(presets.PRESETS[0]["lon"])
        form.addRow("Longitude:", self.lon_spin)

        return widget

    def _build_place_tab(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        search_row = QtWidgets.QHBoxLayout()
        self.place_query = QtWidgets.QLineEdit()
        self.place_query.setPlaceholderText("e.g. Times Square, New York")
        self.place_query.returnPressed.connect(self._on_search_place)
        search_row.addWidget(self.place_query, 1)
        self.search_button = QtWidgets.QPushButton("Search")
        self.search_button.clicked.connect(self._on_search_place)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.results_list = QtWidgets.QListWidget()
        self.results_list.itemSelectionChanged.connect(self._on_result_selected)
        layout.addWidget(self.results_list)

        policy_note = QtWidgets.QLabel(
            "Nominatim (OpenStreetMap) geocoding: max ~1 request/second, "
            "identified with a descriptive User-Agent, no bulk queries. "
            "See geocode.py for the full usage-policy notes."
        )
        policy_note.setWordWrap(True)
        policy_note.setStyleSheet("color: palette(mid); font-size: 10px;")
        layout.addWidget(policy_note)

        return widget

    # ------------------------------------------------------------- logging
    def _log(self, message):
        self.log.appendPlainText(message)
        self.status_label.setText(message)

    # -------------------------------------------------------------- preset
    def _on_preset_changed(self, index):
        if index <= 0:
            return
        preset = presets.PRESETS[index - 1]
        self.tabs.setCurrentIndex(0)
        self.lat_spin.setValue(preset["lat"])
        self.lon_spin.setValue(preset["lon"])
        self.radius_spin.setValue(preset["radius_m"])
        self._log(f"Loaded preset: {preset['label']}")

    # ------------------------------------------------------ output choice
    def _current_mode(self):
        if self.mode_map_radio.isChecked():
            return settings.MODE_2D_MAP
        return settings.MODE_3D_SITE

    def _on_buildings_toggled(self, checked):
        # Track the 2D-mode choice separately: in 3D site mode the checkbox
        # is forced ticked+disabled (buildings are inherent there), so its
        # visible state is not the user's 2D choice.
        if self._current_mode() == settings.MODE_2D_MAP:
            self._map_buildings_choice = checked

    def _on_mode_changed(self):
        is_map = self._current_mode() == settings.MODE_2D_MAP
        self.provider_combo.setEnabled(is_map)
        self.terrain_checkbox.setEnabled(not is_map)
        self.buildings_checkbox.setEnabled(is_map)
        # 3D site mode always includes buildings, as in v0.2; show the
        # checkbox ticked-but-disabled so that stays visible.
        self.buildings_checkbox.setChecked(True if not is_map else self._map_buildings_choice)

    def _load_settings(self):
        saved = settings.load_settings()
        self._map_buildings_choice = saved["include_buildings"]
        self.mode_map_radio.setChecked(saved["output_mode"] == settings.MODE_2D_MAP)
        self.mode_site_radio.setChecked(saved["output_mode"] == settings.MODE_3D_SITE)
        idx = self.provider_combo.findData(saved["imagery_provider"])
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        self._on_mode_changed()

    # ------------------------------------------------------------ geocode
    def _on_search_place(self):
        query = self.place_query.text().strip()
        if not query:
            return
        self.search_button.setEnabled(False)
        self.results_list.clear()
        self._log(f"Searching Nominatim for “{query}”…")
        QtWidgets.QApplication.processEvents()
        try:
            results = geocode.geocode_search(query, limit=5)
        except geocode.GeocodeError as exc:
            self._log(f"Search failed: {exc}")
            results = []
        finally:
            self.search_button.setEnabled(True)

        if not results:
            self._log("No results.")
            return
        for r in results:
            item = QtWidgets.QListWidgetItem(
                f"{r['display_name']}  ({r['lat']:.5f}, {r['lon']:.5f})"
            )
            item.setData(QtCore.Qt.UserRole, r)
            self.results_list.addItem(item)
        self._log(f"{len(results)} result(s). Select one to use it.")

    def _on_result_selected(self):
        items = self.results_list.selectedItems()
        if not items:
            return
        result = items[0].data(QtCore.Qt.UserRole)
        self.lat_spin.setValue(result["lat"])
        self.lon_spin.setValue(result["lon"])
        self._log(f"Selected: {result['display_name']}")

    # --------------------------------------------------------- build/fetch
    def _current_target(self):
        if self.tabs.currentIndex() == 1 and not self.results_list.selectedItems():
            raise ValueError(
                "Place-name mode: search and select a result from the list first."
            )
        lat = self.lat_spin.value()
        lon = self.lon_spin.value()
        radius_m = self.radius_spin.value()
        label = self.preset_combo.currentText()
        if label == "(custom)":
            label = f"Custom location ({lat:.5f}, {lon:.5f})"
        bbox = bbox_from_center_radius(lat, lon, radius_m)
        return bbox, label

    def _set_busy(self, busy):
        self.build_button.setEnabled(not busy)
        self.preset_combo.setEnabled(not busy)
        self.tabs.setEnabled(not busy)
        self.radius_spin.setEnabled(not busy)
        self.terrain_checkbox.setEnabled(not busy)
        self.mode_map_radio.setEnabled(not busy)
        self.mode_site_radio.setEnabled(not busy)
        self.provider_combo.setEnabled(not busy)
        self.buildings_checkbox.setEnabled(not busy)
        if not busy:
            self._on_mode_changed()  # restore mode-dependent enable states

    def _on_fetch_and_build(self):
        try:
            bbox, label = self._current_target()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "SiteContext", str(exc))
            return

        mode = self._current_mode()
        provider_key = self.provider_combo.currentData()
        plan = settings.fetch_plan(
            mode, self._map_buildings_choice, self.terrain_checkbox.isChecked()
        )
        settings.save_settings(mode, self._map_buildings_choice, provider_key)

        self._bbox = bbox
        self._label = label
        self._set_busy(True)
        self.progress_bar.setRange(0, 0)  # indeterminate during fetch
        self._log(f"Starting fetch for {label} ...")

        self._worker = FetchWorker(bbox, label, plan, provider_key, self)
        self._worker.progress.connect(self._log)
        self._worker.finished_ok.connect(self._on_fetch_finished)
        self._worker.failed.connect(self._on_fetch_failed)
        self._worker.start()

    def _on_fetch_failed(self, message):
        self._log(f"FAILED: {message}")
        self._set_busy(False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        QtWidgets.QMessageBox.critical(self, "SiteContext", f"Fetch failed:\n{message}")

    def _on_build_progress(self, current, total, phase):
        if self.progress_bar.maximum() != total:
            self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)
        self.status_label.setText(f"{phase} {current}/{total}…")
        # Pump the event loop so the UI stays responsive while this
        # synchronous FreeCAD-API geometry loop runs on the main thread
        # (it cannot safely move to the background worker thread -- see
        # site_builder.py's module docstring).
        QtWidgets.QApplication.processEvents()

    def _on_fetch_finished(self, payload):
        self._log("Fetch complete. Building geometry…")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        plan = payload.get("plan") or {"imagery": False, "buildings": True}
        try:
            import FreeCAD
            import Part
            import Mesh

            doc_name = _unique_doc_name(FreeCAD, self._label)
            doc, stats = site_builder.build_site(
                FreeCAD,
                Part,
                Mesh,
                doc_name,
                self._bbox,
                self._label,
                payload["osm_data"],
                elevation_grid=payload.get("elevation_grid"),
                elevation_sample_points=payload.get("sample_points"),
                progress_cb=self._on_build_progress,
                include_buildings=plan["buildings"],
                imagery=payload.get("imagery"),
                ground_plane=not plan["imagery"],
            )
        except Exception as exc:  # noqa: BLE001
            self._on_fetch_failed(f"{exc}\n{traceback.format_exc()}")
            return

        self.last_document = doc
        self._set_busy(False)
        self._show_stats(stats)
        _activate_document_view(doc)

    def _show_stats(self, stats):
        if stats.get("imagery"):
            im = stats["imagery"]
            if im["cached"]:
                tiles = "cached image, 0 requests"
            else:
                tiles = f"{im['tiles_fetched']} tiles"
                if im["tiles_failed"]:
                    tiles += f", {im['tiles_failed']} unavailable (gray)"
            if stats["include_buildings"]:
                bldg = f"{stats['built']} building(s) on top"
            else:
                bldg = 'buildings not included (tick "Include 3D buildings" to add them)'
            summary = (
                f"Done: 2D map {im['width_m']:.0f}m x {im['height_m']:.0f}m "
                f"from {im['provider_label']} (z{im['zoom']}, {tiles}); {bldg}"
            )
            self._log(summary)
            self.status_label.setText(summary)
            return

        terrain_info = stats["terrain"]
        terrain_line = (
            f"terrain: {'MESH (relief ' + format(terrain_info['relief_m'], '.1f') + 'm)' if terrain_info['used'] else 'flat plane'}"
            if terrain_info["attempted"]
            else "terrain: not requested"
        )
        summary = (
            f"Done: {stats['built']} building(s) from ways "
            f"({stats['skipped']} skipped), "
            f"{stats['relations_built']} from relations "
            f"({stats['relations_skipped']} skipped); "
            f"ground {stats['ground_w_m']:.0f}m x {stats['ground_d_m']:.0f}m; "
            f"{terrain_line}"
        )
        self._log(summary)
        self.status_label.setText(summary)


def _unique_doc_name(FreeCAD, label):
    import re

    base = re.sub(r"[^A-Za-z0-9_]+", "_", label)[:40] or "SiteContext"
    name = base
    i = 1
    while name in FreeCAD.listDocuments():
        i += 1
        name = f"{base}_{i}"
    return name


def _activate_document_view(doc):
    """Make the freshly built document actually visible/focused: activate
    it, switch to isometric, and fit the view. Mirrors the three headless-
    FreeCAD gotchas documented in sitecontext_proto.py/view_setup.py --
    here running inside a real GUI session (not freecadcmd), FreeCAD does
    create a view automatically, but it may not be the *frontmost* MDI
    tab, so we still explicitly raise + fit it.
    """
    try:
        Gui.setActiveDocument(doc.Name)
        gdoc = Gui.ActiveDocument
        if gdoc is None:
            return
        view = gdoc.ActiveView
        if view is not None:
            view.viewIsometric()
            Gui.SendMsgToActiveView("ViewFit")
    except Exception:  # noqa: BLE001 - best-effort view polish, never fatal
        pass
