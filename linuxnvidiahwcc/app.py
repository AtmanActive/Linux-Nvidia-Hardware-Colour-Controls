"""Qt GUI: hardware colour sliders, per-display settings, tray icon, single instance."""

from __future__ import annotations

import os
import sys
import threading

from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QMenu, QMessageBox, QPushButton, QSlider, QSystemTrayIcon, QToolButton,
    QVBoxLayout, QWidget,
)

from . import __version__, autostart, config, engine, kwin
from . import colormath as cm

APP_NAME = "Linux Nvidia Hardware Colour Controls"
REPO_URL = "https://github.com/AtmanActive/Linux-Nvidia-Hardware-Colour-Controls"
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "linuxnvidiahwcc.svg")
SOCKET_NAME = f"{config.APP_ID}-{os.getuid()}"

LEVEL_COLOURS = {engine.OK: "#3fb950", engine.WARN: "#d29922", engine.ERROR: "#f85149", engine.OFF: "#8b949e"}


def app_icon() -> QIcon:
    icon = QIcon.fromTheme(config.APP_ID)
    if icon.isNull():
        icon = QIcon(ICON_PATH)
    return icon


# --- background worker --------------------------------------------------------

class Worker(QThread):
    """Runs all slow work (kscreen-doctor, helper) off the GUI thread.

    Requests are coalesced: while a slider is dragged only the newest value
    is applied.

    While the window is visible ("active"), a watchdog runs every few seconds
    to re-check vibrance (the hardware loses it on suspend/DPMS/modeset) and
    to watch for display hotplug. While the app sits in the tray the thread
    blocks with no timeout: zero wakeups until the window is restored or the
    app exits.
    """

    displaysChanged = Signal(object)  # list[engine.Display]
    statusChanged = Signal(str, object)  # name, engine.Status
    problem = Signal(str)

    WATCHDOG_SECONDS = 3.0

    def __init__(self, eng: engine.Engine):
        super().__init__()
        self.eng = eng
        self.cv = threading.Condition()
        self.dirty: dict[str, set[str]] = {}
        self.verify_names: set[str] = set()
        self.refresh_pending = False
        self.apply_all_pending = False
        self.stopping = False
        self.active = False  # window visible -> watchdog allowed
        self.enforce_pending = False
        self.signature = engine.hotplug_signature()

    def set_active(self, on: bool) -> None:
        """Window shown (True) or hidden to tray (False)."""
        with self.cv:
            if on and not self.active:
                # Coming back from the tray: catch up on anything missed while
                # dormant (hotplug, vibrance lost during suspend/DPMS).
                self.refresh_pending = True
                self.enforce_pending = True
            self.active = on
            self.cv.notify()

    # called from GUI thread
    def request_apply(self, name: str, *kinds: str) -> None:
        with self.cv:
            self.dirty.setdefault(name, set()).update(kinds or ("tone", "vibrance"))
            self.cv.notify()

    def request_refresh(self, apply_all: bool = False) -> None:
        with self.cv:
            self.refresh_pending = True
            self.apply_all_pending |= apply_all
            self.cv.notify()

    def request_verify(self, name: str) -> None:
        with self.cv:
            self.verify_names.add(name)
            self.cv.notify()

    def stop(self) -> None:
        with self.cv:
            self.stopping = True
            self.cv.notify()
        self.wait(3000)

    def run(self) -> None:
        while True:
            with self.cv:
                if not (self.dirty or self.refresh_pending or self.verify_names
                        or self.enforce_pending or self.stopping):
                    # Visible: periodic watchdog. In the tray: sleep until
                    # explicitly woken (restore or exit), no timer at all.
                    self.cv.wait(self.WATCHDOG_SECONDS if self.active else None)
                if self.stopping:
                    return
                dirty, self.dirty = self.dirty, {}
                verify, self.verify_names = self.verify_names, set()
                refresh, self.refresh_pending = self.refresh_pending, False
                apply_all, self.apply_all_pending = self.apply_all_pending, False
                enforce, self.enforce_pending = self.enforce_pending, False
                active = self.active

            if not (dirty or verify or refresh or enforce):
                if active:
                    self._watchdog()
                continue

            if refresh:
                self._refresh(apply_all)
                verify |= set(self.eng.displays)

            if enforce:
                self.eng.enforce_vibrance()  # verified below with the rest

            for name, kinds in dirty.items():
                try:
                    if "tone" in kinds:
                        self.eng.apply_tone(name)
                    if "vibrance" in kinds:
                        self.eng.apply_vibrance(name)
                except Exception as e:  # noqa: BLE001 - report anything to the user
                    self.problem.emit(f"{name}: {e}")
                verify.add(name)

            if verify:
                self.msleep(300)  # let KWin commit the new pipeline
                with self.cv:
                    busy = bool(self.dirty)
                if busy:  # more changes queued; verify after those
                    with self.cv:
                        self.verify_names |= verify
                    continue
                for name in verify:
                    try:
                        # Hardware-only: withdraw the profile if KWin did not
                        # put it in the hardware LUT (it would be a shader).
                        self.eng.ensure_hardware(name)
                        self.statusChanged.emit(name, self.eng.verify(name))
                    except Exception as e:  # noqa: BLE001
                        self.problem.emit(f"{name}: {e}")

    def _refresh(self, apply_all: bool) -> None:
        try:
            displays = self.eng.refresh()
        except Exception as e:  # noqa: BLE001
            self.problem.emit(str(e))
            return
        self.signature = engine.hotplug_signature()
        self.displaysChanged.emit(displays)
        for w in self.eng.warnings:
            self.problem.emit(w)
        if apply_all:
            for err in self.eng.apply_all():
                self.problem.emit(err)

    def _watchdog(self) -> None:
        sig = engine.hotplug_signature()
        if sig != self.signature:
            self.msleep(1500)  # let KWin finish configuring the new output
            self._refresh(apply_all=True)
            for name in self.eng.displays:
                self.request_verify(name)
            return
        for name in self.eng.enforce_vibrance():
            self.request_verify(name)


# --- widgets ------------------------------------------------------------------

class ResettableSlider(QSlider):
    """QSlider that jumps back to its default value on double-click."""

    def __init__(self, default: int):
        super().__init__(Qt.Horizontal)
        self.default = default
        self.setToolTip("Double-click to reset to default")

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.setValue(self.default)
            ev.accept()
        else:
            super().mouseDoubleClickEvent(ev)


class SliderRow:
    def __init__(self, grid: QGridLayout, row: int, title: str, lo: int, hi: int, fmt,
                 default: int, step: int = 1):
        self.fmt = fmt
        self.label = QLabel(title)
        self.minus = QToolButton(text="−")
        self.plus = QToolButton(text="+")
        for b, d in ((self.minus, -step), (self.plus, step)):
            b.setAutoRepeat(True)
            b.setAutoRaise(True)
            b.clicked.connect(lambda _=False, d=d: self.slider.setValue(self.slider.value() + d))
        self.slider = ResettableSlider(default)
        self.slider.setRange(lo, hi)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(step * 10)
        self.slider.setMinimumWidth(260)
        self.value = QLabel()
        self.value.setMinimumWidth(56)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.slider.valueChanged.connect(lambda v: self.value.setText(self.fmt(v)))
        grid.addWidget(self.label, row, 0)
        grid.addWidget(self.minus, row, 1)
        grid.addWidget(self.slider, row, 2)
        grid.addWidget(self.plus, row, 3)
        grid.addWidget(self.value, row, 4)

    def set_silently(self, v: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.value.setText(self.fmt(self.slider.value()))

    def setEnabled(self, on: bool) -> None:
        for w in (self.label, self.minus, self.slider, self.plus, self.value):
            w.setEnabled(on)


class StatusRow(QWidget):
    def __init__(self, title: str):
        super().__init__()
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 4)
        lay.setVerticalSpacing(0)
        self.dot = QLabel("●")
        self.title = QLabel(f"<b>{title}</b>")
        self.text = QLabel("…")
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.dot, 0, 0)
        lay.addWidget(self.title, 0, 1)
        lay.addWidget(self.text, 1, 1)
        lay.setColumnStretch(1, 1)

    def set(self, check: engine.Check) -> None:
        self.dot.setStyleSheet(f"color: {LEVEL_COLOURS.get(check.level, '#8b949e')};")
        self.text.setText(check.text)


class MainWindow(QWidget):
    exitRequested = Signal()

    def __init__(self, eng: engine.Engine, worker: Worker):
        super().__init__()
        self.eng, self.worker = eng, worker
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.displays: list[engine.Display] = []
        self.save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save)
        self._activity_timer = QTimer(self, singleShot=True, interval=300, timeout=self._sync_activity)

        root = QVBoxLayout(self)
        root.setSizeConstraint(QVBoxLayout.SetMinimumSize)  # grow when status text wraps
        self.setMinimumWidth(560)

        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Display:</b>"))
        self.combo = QComboBox()
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.combo.currentIndexChanged.connect(self._display_selected)
        top.addWidget(self.combo, 1)
        self.refresh_btn = QToolButton(text="⟳")
        self.refresh_btn.setToolTip("Re-detect displays")
        self.refresh_btn.clicked.connect(lambda: self.worker.request_refresh())
        top.addWidget(self.refresh_btn)
        root.addLayout(top)

        colour = QGroupBox("Colour settings")
        g = QGridLayout(colour)
        d = cm.DEFAULTS
        self.brightness = SliderRow(g, 0, "Brightness", *cm.BRIGHTNESS_RANGE, lambda v: f"{v}%",
                                    default=d["brightness"])
        self.contrast = SliderRow(g, 1, "Contrast", *cm.CONTRAST_RANGE, lambda v: f"{v}%",
                                  default=d["contrast"])
        self.gamma = SliderRow(g, 2, "Gamma", int(cm.GAMMA_RANGE[0] * 100), int(cm.GAMMA_RANGE[1] * 100),
                               lambda v: f"{v / 100:.2f}", default=round(d["gamma"] * 100))
        root.addWidget(colour)

        enh = QGroupBox("Enhancements")
        g2 = QGridLayout(enh)
        self.vibrance = SliderRow(g2, 0, "Digital vibrance", *cm.VIBRANCE_RANGE, lambda v: f"{v}%",
                                  default=cm.DEFAULTS["vibrance"])
        root.addWidget(enh)

        status = QGroupBox("Hardware status (read back from the GPU)")
        sl = QVBoxLayout(status)
        self.st_tone = StatusRow("Brightness / Contrast / Gamma")
        self.st_vib = StatusRow("Digital vibrance")
        for w in (self.st_tone, self.st_vib):
            sl.addWidget(w)
        self.problem_label = QLabel()
        self.problem_label.setWordWrap(True)
        self.problem_label.setStyleSheet(f"color: {LEVEL_COLOURS[engine.ERROR]};")
        self.problem_label.hide()
        sl.addWidget(self.problem_label)
        root.addWidget(status)

        opts = QHBoxLayout()
        self.autostart = QCheckBox("Start at login (minimised to tray)")
        self.autostart.setChecked(autostart.is_enabled())
        self.autostart.toggled.connect(self._autostart_toggled)
        opts.addWidget(self.autostart)
        opts.addStretch(1)
        root.addLayout(opts)

        buttons = QHBoxLayout()
        self.defaults_btn = QPushButton("Reset Colours")
        self.defaults_btn.clicked.connect(self._restore_defaults)
        buttons.addWidget(self.defaults_btn)
        buttons.addStretch(1)
        muted = self.palette().color(QPalette.PlaceholderText).name()
        about = QLabel(f'<a href="{REPO_URL}" style="color: {muted}; text-decoration: none;">'
                       f'<small>v{__version__}</small></a>')
        about.setOpenExternalLinks(True)  # opens in the default browser
        about.setCursor(Qt.PointingHandCursor)
        about.setToolTip(REPO_URL)
        buttons.addWidget(about)
        exit_btn = QPushButton("Exit")
        exit_btn.setToolTip("Quit the application. Settings already applied stay active.")
        exit_btn.clicked.connect(self.exitRequested.emit)
        buttons.addWidget(exit_btn)
        close = QPushButton("Close to tray")
        close.clicked.connect(self.hide)
        buttons.addWidget(close)
        root.addLayout(buttons)

        for row, kind in ((self.brightness, "tone"), (self.contrast, "tone"), (self.gamma, "tone"),
                          (self.vibrance, "vibrance")):
            row.slider.valueChanged.connect(lambda _v, k=kind: self._changed(k))
        self._set_controls_enabled(False)

        worker.displaysChanged.connect(self.set_displays)
        worker.statusChanged.connect(self._status)
        worker.problem.connect(self._problem)

    # --- data flow ---
    def current_name(self) -> str | None:
        i = self.combo.currentIndex()
        return self.displays[i].name if 0 <= i < len(self.displays) else None

    def set_displays(self, displays: list) -> None:
        prev = self.current_name() or self.eng.cfg.selected_output
        self.displays = displays
        self.combo.blockSignals(True)
        self.combo.clear()
        for d in displays:
            self.combo.addItem(d.label)
        idx = next((i for i, d in enumerate(displays) if d.name == prev), 0)
        self.combo.setCurrentIndex(idx if displays else -1)
        self.combo.blockSignals(False)
        self._set_controls_enabled(bool(displays))
        self._display_selected()
        if not displays:
            self._problem("No enabled displays found via kscreen-doctor.")

    def _display_selected(self) -> None:
        name = self.current_name()
        if not name:
            return
        with self.eng.lock:
            s = self.eng.cfg.output(name)
            self.eng.cfg.selected_output = name
        self.brightness.set_silently(s.brightness)
        self.contrast.set_silently(s.contrast)
        self.gamma.set_silently(round(s.gamma * 100))
        self.vibrance.set_silently(s.vibrance)
        d = self.displays[self.combo.currentIndex()]
        self.vibrance.setEnabled(d.port is not None)
        self.save_timer.start()
        self.worker.request_verify(name)

    def _changed(self, kind: str) -> None:
        name = self.current_name()
        if not name:
            return
        with self.eng.lock:
            s = self.eng.cfg.output(name)
            s.brightness = self.brightness.slider.value()
            s.contrast = self.contrast.slider.value()
            s.gamma = self.gamma.slider.value() / 100
            s.vibrance = self.vibrance.slider.value()
            self.eng.cfg.set_output(name, s)
        self.worker.request_apply(name, kind)
        self.save_timer.start()

    def _save(self) -> None:
        with self.eng.lock:
            config.save(self.eng.cfg)

    def _restore_defaults(self) -> None:
        name = self.current_name()
        if not name:
            return
        with self.eng.lock:
            self.eng.cfg.set_output(name, config.OutputSettings())
        self._display_selected()
        self.worker.request_apply(name, "tone", "vibrance")

    def _set_controls_enabled(self, on: bool) -> None:
        for r in (self.brightness, self.contrast, self.gamma, self.vibrance):
            r.setEnabled(on)
        self.defaults_btn.setEnabled(on)

    def _autostart_toggled(self, on: bool) -> None:
        try:
            autostart.set_enabled(on)
        except OSError as e:
            self._problem(f"Autostart: {e}")

    def _status(self, name: str, st: engine.Status) -> None:
        if name != self.current_name():
            return
        self.st_tone.set(st.tone)
        self.st_vib.set(st.vibrance)
        self.problem_label.hide()

    def _problem(self, text: str) -> None:
        self.problem_label.setText(text)
        self.problem_label.show()

    def closeEvent(self, ev) -> None:  # close button hides to tray
        ev.ignore()
        self.hide()

    # Worker activity follows what the user can actually see:
    #   window shown and exposed  -> active (watchdog runs)
    #   hidden in tray, minimised -> dormant (no timers, no wakeups)
    # On Wayland a minimised window is not "hidden"; KWin marks it suspended,
    # which Qt reports as an Expose event with isExposed() == False.
    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        handle = self.windowHandle()
        if handle is not None and not getattr(self, "_watching_handle", False):
            handle.installEventFilter(self)
            self._watching_handle = True
        self._activity_timer.start()  # debounced: let the window map first

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._activity_timer.stop()
        self.worker.set_active(False)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Expose and obj is self.windowHandle():
            if obj.isExposed():
                self._activity_timer.start()
            else:  # minimised: go dormant immediately
                self._activity_timer.stop()
                self.worker.set_active(False)
        return False

    def _sync_activity(self) -> None:
        handle = self.windowHandle()
        exposed = handle is not None and handle.isExposed()
        self.worker.set_active(self.isVisible() and exposed and not self.isMinimized())

    def show_and_raise(self) -> None:
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()


# --- tray / application -------------------------------------------------------

class Controller(QObject):
    def __init__(self, app: QApplication, eng: engine.Engine, show: bool):
        super().__init__()
        self.app = app
        self.worker = Worker(eng)
        self.window = MainWindow(eng, self.worker)
        self.window.exitRequested.connect(self.quit)

        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        act_show = QAction("Show controls", menu, triggered=self.window.show_and_raise)
        act_apply = QAction("Re-apply settings", menu, triggered=lambda: self.worker.request_refresh(True))
        act_quit = QAction("Quit", menu, triggered=self.quit)
        menu.addAction(act_show)
        menu.addAction(act_apply)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.menu = menu
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

        self.server = QLocalServer(self)
        QLocalServer.removeServer(SOCKET_NAME)
        self.server.listen(SOCKET_NAME)
        self.server.newConnection.connect(self._other_instance)

        self.worker.problem.connect(lambda t: self.tray.setToolTip(f"{APP_NAME}\n{t}"))
        self.worker.start()
        self.worker.request_refresh(apply_all=True)
        if show or not QSystemTrayIcon.isSystemTrayAvailable():
            self.window.show_and_raise()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.window.isVisible():
                self.window.hide()
            else:
                self.window.show_and_raise()

    def _other_instance(self) -> None:
        sock = self.server.nextPendingConnection()
        if sock is None:
            return
        sock.waitForReadyRead(500)
        if bytes(sock.readAll()).strip() == b"show":
            self.window.show_and_raise()
        sock.disconnectFromServer()

    def quit(self) -> None:
        self.worker.stop()
        self.tray.hide()
        self.app.quit()


def notify_running_instance(show: bool) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"show\n" if show else b"noop\n")
    sock.flush()
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def run_gui(show: bool) -> int:
    QApplication.setApplicationName(config.APP_ID)
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setDesktopFileName(config.APP_ID)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    if notify_running_instance(show):
        return 0
    if not kwin.available():
        QMessageBox.critical(None, APP_NAME, "kscreen-doctor was not found. This tool needs KDE Plasma 6.")
        return 1

    eng = engine.Engine(config.load())
    ctl = Controller(app, eng, show)  # noqa: F841 - keep a reference
    rc = app.exec()
    with eng.lock:
        config.save(eng.cfg)
    return rc
