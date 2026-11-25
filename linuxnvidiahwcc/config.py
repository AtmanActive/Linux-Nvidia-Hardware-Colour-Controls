"""Persistent settings (~/.config/linuxnvidiahwcc/config.json)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass

from . import colormath as cm

APP_ID = "linuxnvidiahwcc"


def _xdg(var: str, default: str) -> str:
    return os.environ.get(var) or os.path.expanduser(default)


CONFIG_DIR = os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config"), APP_ID)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
DATA_DIR = os.path.join(_xdg("XDG_DATA_HOME", "~/.local/share"), APP_ID)
PROFILE_DIR = os.path.join(DATA_DIR, "profiles")
AUTOSTART_FILE = os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config"), "autostart", f"{APP_ID}.desktop")


@dataclass
class OutputSettings:
    brightness: int = cm.DEFAULTS["brightness"]
    contrast: int = cm.DEFAULTS["contrast"]
    gamma: float = cm.DEFAULTS["gamma"]
    vibrance: int = cm.DEFAULTS["vibrance"]

    @classmethod
    def from_dict(cls, d: dict) -> "OutputSettings":
        s = cls()
        for k in asdict(s):
            if k in d:
                setattr(s, k, type(getattr(s, k))(d[k]))
        s.clamp()
        return s

    def clamp(self) -> None:
        self.brightness = int(min(max(self.brightness, cm.BRIGHTNESS_RANGE[0]), cm.BRIGHTNESS_RANGE[1]))
        self.contrast = int(min(max(self.contrast, cm.CONTRAST_RANGE[0]), cm.CONTRAST_RANGE[1]))
        self.gamma = round(float(min(max(self.gamma, cm.GAMMA_RANGE[0]), cm.GAMMA_RANGE[1])), 2)
        self.vibrance = int(min(max(self.vibrance, cm.VIBRANCE_RANGE[0]), cm.VIBRANCE_RANGE[1]))

    def tone_is_neutral(self) -> bool:
        return (self.brightness == cm.DEFAULTS["brightness"]
                and self.contrast == cm.DEFAULTS["contrast"]
                and abs(self.gamma - 1.0) < 1e-6)


@dataclass
class Config:
    outputs: dict
    original: dict  # KWin profile state before we first touched each output
    selected_output: str = ""

    def output(self, name: str) -> OutputSettings:
        return OutputSettings.from_dict(self.outputs.get(name, {}))

    def has_output(self, name: str) -> bool:
        return name in self.outputs

    def set_output(self, name: str, s: OutputSettings) -> None:
        self.outputs[name] = asdict(s)


def load() -> Config:
    try:
        with open(CONFIG_FILE) as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        d = {}
    return Config(
        # normalise: drops unknown/obsolete keys (e.g. "hue" from older versions)
        outputs={k: asdict(OutputSettings.from_dict(v)) for k, v in d.get("outputs", {}).items()},
        original=d.get("original", {}),
        selected_output=d.get("selected_output", ""),
    )


def save(cfg: Config) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    data = {
        "version": 1,
        "outputs": cfg.outputs,
        "original": cfg.original,
        "selected_output": cfg.selected_output,
    }
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CONFIG_FILE)
