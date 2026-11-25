"""Discovery, applying and verifying colour settings (no GUI code here)."""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field

from . import colormath as cm
from . import config, drm, icc, kwin, nvkms

OK, WARN, ERROR, OFF = "ok", "warn", "error", "off"


@dataclass
class Display:
    name: str  # connector name as KDE uses it, e.g. HDMI-A-2
    label: str
    kwin: kwin.KWinOutput
    nvidia: bool
    port: nvkms.Port | None = None


@dataclass
class Check:
    level: str
    text: str


@dataclass
class Status:
    tone: Check = field(default_factory=lambda: Check(OFF, "…"))
    vibrance: Check = field(default_factory=lambda: Check(OFF, "…"))


def _edid_name(edid: bytes) -> str:
    for off in range(54, 126, 18):
        block = edid[off: off + 18]
        if len(block) == 18 and block[:3] == b"\0\0\0" and block[3] == 0xFC:
            return block[5:].split(b"\n")[0].decode("ascii", "replace").strip()
    return ""


def _sysfs_edid(connector: str) -> bytes:
    card = drm.card_of_connector(connector)
    if not card:
        return b""
    try:
        with open(f"/sys/class/drm/{os.path.basename(card)}-{connector}/edid", "rb") as f:
            return f.read()
    except OSError:
        return b""


def hotplug_signature() -> str:
    parts = []
    base = "/sys/class/drm"
    try:
        for entry in sorted(os.listdir(base)):
            if "-" in entry and entry.startswith("card"):
                try:
                    with open(f"{base}/{entry}/status") as f:
                        parts.append(f"{entry}={f.read().strip()}")
                except OSError:
                    pass
    except OSError:
        pass
    return ";".join(parts)


class Engine:
    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.displays: dict[str, Display] = {}
        self.warnings: list[str] = []
        self.withdrawn: dict[str, str] = {}  # output -> reason tone settings are not applied

    # --- discovery -----------------------------------------------------------
    def refresh(self) -> list[Display]:
        warnings: list[str] = []
        outs = [o for o in kwin.outputs() if o.connected and o.enabled]

        ports: list[nvkms.Port] = []
        try:
            ports = [p for p in nvkms.list_ports() if p.connected]
        except nvkms.NvkmsError as e:
            warnings.append(f"Digital Vibrance unavailable: {e}")

        nv_conns = drm.connector_map()
        displays: dict[str, Display] = {}
        for o in outs:
            port = None
            conn = nv_conns.get(o.name)
            if conn is not None:
                # nvidia-drm creates one CRTC per NVKMS head, in order.
                port = next((p for p in ports if p.head is not None and p.head == conn.crtc_index), None)
                if port is None:
                    active = [c for c in nv_conns.values() if c.connected]
                    if len(active) == 1 and len(ports) == 1:
                        port = ports[0]
            if port is not None:
                label = re.sub(r"\s*\([^)]*\)\s*$", "", port.name).strip()
            else:
                label = _edid_name(_sysfs_edid(o.name))
            label = f"{label} ({o.name})" if label else o.name
            displays[o.name] = Display(o.name, label, o, conn is not None, port)

        with self.lock:
            # First sighting of a display: start from what the hardware has now,
            # so launching the app never silently resets an existing vibrance.
            for d in displays.values():
                if not self.cfg.has_output(d.name):
                    s = config.OutputSettings()
                    if d.port and d.port.vibrance is not None:
                        s.vibrance = round(50 + d.port.vibrance / 1024 * 50)
                        s.clamp()
                    self.cfg.set_output(d.name, s)
            config.save(self.cfg)
            self.displays = displays
            self.warnings = warnings
        return list(displays.values())

    # --- applying ------------------------------------------------------------
    def _is_our_profile(self, path: str) -> bool:
        return bool(path) and os.path.abspath(path).startswith(os.path.abspath(config.PROFILE_DIR) + os.sep)

    def _restore_original(self, d: Display) -> None:
        """Give KWin back the colour profile it had before we touched this output."""
        with self.lock:
            original = dict(self.cfg.original.get(d.name, {}))
        src = original.get("source") or "sRGB"
        ipath = original.get("icc") or ""
        if src == "ICC" and not (ipath and os.path.exists(ipath)):
            src, ipath = "sRGB", ""
        if d.kwin.profile_source == src and (src != "ICC" or d.kwin.icc_path == ipath):
            return
        kwin.set_profile_source(d.name, src, ipath if src == "ICC" else None)
        d.kwin.profile_source = src
        if src == "ICC":
            d.kwin.icc_path = ipath

    def _our_profile_active(self, d: Display) -> bool:
        return d.kwin.profile_source == "ICC" and self._is_our_profile(d.kwin.icc_path)

    def apply_tone(self, name: str) -> None:
        with self.lock:
            d = self.displays.get(name)
            if d is None:
                return
            s = self.cfg.output(name)
            if name not in self.cfg.original:
                if self._our_profile_active(d):
                    self.cfg.original[name] = {"source": "sRGB", "icc": ""}
                else:
                    self.cfg.original[name] = {"source": d.kwin.profile_source or "sRGB",
                                               "icc": d.kwin.icc_path}
                config.save(self.cfg)
            self.withdrawn.pop(name, None)

        if s.tone_is_neutral():
            self._restore_original(d)
            return
        if d.kwin.hdr_enabled:
            # KWin does not use the SDR profile in HDR mode; nothing to do in hardware.
            self._restore_original(d)
            with self.lock:
                self.withdrawn[name] = "HDR is enabled on this display. KWin's HDR pipeline " \
                                       "does not use these settings, so they are not applied."
            return

        os.makedirs(config.PROFILE_DIR, exist_ok=True)
        # KWin only reloads a profile when the path changes, so alternate.
        a = os.path.join(config.PROFILE_DIR, f"{name}-a.icc")
        b = os.path.join(config.PROFILE_DIR, f"{name}-b.icc")
        path = b if d.kwin.icc_path == a else a
        icc.write_profile(path, brightness=s.brightness, contrast=s.contrast, gamma=s.gamma)
        kwin.set_icc_profile(name, path)
        d.kwin.icc_path = path
        d.kwin.profile_source = "ICC"
        d.kwin.power_preference = "prefer efficiency and performance"

    def ensure_hardware(self, name: str, attempts: int = 3) -> bool:
        """Hardware-only guarantee for Brightness/Contrast/Gamma.

        If our profile is active but KWin did not program the CRTC GAMMA_LUT
        (meaning it would render the profile in a shader), withdraw the profile.
        Returns True when the settings are in hardware (or nothing is applied).
        """
        d = self.displays.get(name)
        if d is None or not self._our_profile_active(d):
            return True
        for i in range(attempts):
            try:
                cc = drm.read_crtc_color(name)
            except OSError:
                cc = None
            if cc is not None and cc.gamma_lut is not None:
                return True
            time.sleep(0.4)  # KWin may still be committing the new pipeline
        self._restore_original(d)
        with self.lock:
            self.withdrawn[name] = ("Not applied: KWin could not place these settings in the hardware LUT "
                                    "and would have rendered them in software, so they were withdrawn. "
                                    "Move a slider or use 'Re-apply' to try again.")
        return False

    def apply_vibrance(self, name: str) -> None:
        with self.lock:
            d = self.displays.get(name)
            if d is None or d.port is None:
                return
            dvc = cm.vibrance_to_dvc(self.cfg.output(name).vibrance)
        got = nvkms.set_vibrance(d.port.disp, d.port.dpy_id, dvc)
        d.port.vibrance = got

    def apply_all(self) -> list[str]:
        errors = []
        for name in list(self.displays):
            for fn in (self.apply_tone, self.apply_vibrance):
                try:
                    fn(name)
                except (kwin.KWinError, nvkms.NvkmsError, OSError) as e:
                    errors.append(f"{name}: {e}")
        return errors

    def enforce_vibrance(self) -> list[str]:
        """Re-apply vibrance where the hardware lost it (suspend, DPMS, modeset)."""
        fixed = []
        for name, d in list(self.displays.items()):
            if d.port is None:
                continue
            with self.lock:
                want = cm.vibrance_to_dvc(self.cfg.output(name).vibrance)
            try:
                have = nvkms.get_vibrance(d.port.disp, d.port.dpy_id)
                if have is not None and have != want:
                    d.port.vibrance = nvkms.set_vibrance(d.port.disp, d.port.dpy_id, want)
                    fixed.append(name)
            except nvkms.NvkmsError:
                pass
        return fixed

    # --- verification --------------------------------------------------------
    def verify(self, name: str) -> Status:
        st = Status()
        with self.lock:
            d = self.displays.get(name)
            if d is None:
                return st
            s = self.cfg.output(name)
            withdrawn = self.withdrawn.get(name)

        neutral = s.tone_is_neutral()
        try:
            cc = drm.read_crtc_color(name)
        except OSError:
            cc = None

        if withdrawn:
            st.tone = Check(ERROR, withdrawn)
        elif cc is None:
            st.tone = Check(WARN, "Could not read the display's hardware LUT state.")
        elif neutral:
            st.tone = Check(OK, "Neutral: no adjustment applied.")
        elif not self._our_profile_active(d):
            st.tone = Check(WARN, "Not applied yet.")
        elif cc.gamma_lut is None:
            st.tone = Check(ERROR, "Not in the hardware LUT. Use 'Re-apply' to retry.")
        else:
            lut = cc.gamma_lut
            want = cm.ramp(len(lut), s.brightness, s.contrast, s.gamma)
            delta = max(abs(lut[i][ch] - want[i]) for i in range(len(lut)) for ch in range(3))
            if delta < 0.01:
                st.tone = Check(OK, f"Hardware: CRTC GAMMA_LUT ({len(lut)} entries, verified).")
            else:
                nl = kwin.night_light_temperature()
                if nl is not None and nl < 6500:
                    st.tone = Check(OK, f"Hardware: CRTC GAMMA_LUT ({len(lut)} entries), combined by KWin "
                                        f"with Night Light ({nl} K) in the same LUT.")
                else:
                    st.tone = Check(WARN, f"Hardware: CRTC GAMMA_LUT is programmed but differs from these "
                                          f"settings (Δmax {delta:.3f}); another KDE adjustment is combined with it.")

        if d.port is None:
            if d.nvidia:
                st.vibrance = Check(ERROR, "Could not match this output to an NVIDIA display head.")
            else:
                st.vibrance = Check(OFF, "Not an NVIDIA output; Digital Vibrance unavailable.")
        else:
            want = cm.vibrance_to_dvc(s.vibrance)
            try:
                have = nvkms.get_vibrance(d.port.disp, d.port.dpy_id)
            except nvkms.NvkmsError as e:
                have = None
                st.vibrance = Check(ERROR, str(e))
            if have is not None:
                if have == want:
                    st.vibrance = Check(OK, f"Hardware: NVKMS digital vibrance {have:+d} (display engine CSC).")
                else:
                    st.vibrance = Check(WARN, f"Hardware value is {have:+d}, expected {want:+d} (not applied yet).")
        return st
