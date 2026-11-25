"""KWin output control through ``kscreen-doctor`` (KDE Plasma 6, Wayland)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class KWinError(RuntimeError):
    pass


@dataclass
class KWinOutput:
    name: str
    enabled: bool
    connected: bool
    hdr: str = "unknown"  # enabled / disabled / incapable
    icc_path: str = ""
    profile_source: str = ""  # sRGB / ICC / EDID
    power_preference: str = ""  # human readable, from kscreen-doctor -o

    @property
    def prefers_efficiency(self) -> bool:
        return "efficiency" in self.power_preference.lower()

    @property
    def hdr_enabled(self) -> bool:
        return self.hdr.lower().startswith("enabled")


def available() -> bool:
    return shutil.which("kscreen-doctor") is not None


def _run(args: list[str], timeout: float = 10) -> str:
    try:
        p = subprocess.run(
            ["kscreen-doctor", *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as e:
        raise KWinError("kscreen-doctor not found (install libkscreen / KDE Plasma 6)") from e
    except subprocess.TimeoutExpired as e:
        raise KWinError("kscreen-doctor timed out") from e
    out = _ANSI.sub("", p.stdout + p.stderr)
    if p.returncode != 0 or "failed" in out.lower():
        raise KWinError(out.strip() or f"kscreen-doctor exited with {p.returncode}")
    return _ANSI.sub("", p.stdout)


def outputs() -> list[KWinOutput]:
    data = json.loads(_run(["-j"]))
    result = {
        o["name"]: KWinOutput(o["name"], bool(o.get("enabled")), bool(o.get("connected")),
                              icc_path=o.get("iccProfilePath", "") or "")
        for o in data.get("outputs", [])
    }
    # Colour fields are only in the human readable listing.
    current = None
    for line in _run(["-o"]).splitlines():
        m = re.match(r"^Output:\s+\d+\s+(\S+)", line)
        if m:
            current = result.get(m.group(1))
            continue
        if current is None or ":" not in line:
            continue
        key, _, value = line.strip().partition(":")
        value = value.strip()
        key = key.strip().lower()
        if key == "hdr":
            current.hdr = value
        elif key == "color profile source":
            current.profile_source = value
        elif key == "color power preference":
            current.power_preference = value
        elif key == "icc profile" and value.lower() != "none":
            current.icc_path = value
    return list(result.values())


def set_icc_profile(output: str, path: str) -> None:
    """Apply a profile. 'prefer efficiency' is always set: it is what lets KWin
    program the profile's LUT into the hardware instead of using a shader."""
    _run([f"output.{output}.iccprofile.{path}", f"output.{output}.colorProfileSource.ICC",
          f"output.{output}.colorPowerTradeoff.preferEfficiency"])


def set_profile_source(output: str, source: str, icc_path: str | None = None) -> None:
    args = []
    if icc_path:
        args.append(f"output.{output}.iccprofile.{icc_path}")
    args.append(f"output.{output}.colorProfileSource.{source}")
    _run(args)


def night_light_temperature() -> int | None:
    """Current KWin Night Light colour temperature in kelvin (6500 = off)."""
    try:
        p = subprocess.run(
            ["busctl", "--user", "get-property", "org.kde.KWin", "/org/kde/KWin/NightLight",
             "org.kde.KWin.NightLight", "currentTemperature"],
            capture_output=True, text=True, timeout=2)
        return int(p.stdout.split()[1]) if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, IndexError, ValueError):
        return None
