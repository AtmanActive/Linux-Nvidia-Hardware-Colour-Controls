"""XDG autostart entry (~/.config/autostart/linuxnvidiahwcc.desktop)."""

from __future__ import annotations

import os
import shlex
import shutil
import sys

from .config import APP_ID, AUTOSTART_FILE


def _exec_line() -> str:
    exe = shutil.which(APP_ID)
    if exe:
        return shlex.quote(exe)
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"env PYTHONPATH={shlex.quote(pkg_parent)} {shlex.quote(sys.executable)} -m {APP_ID}"


def is_enabled() -> bool:
    return os.path.exists(AUTOSTART_FILE)


def set_enabled(on: bool) -> None:
    if not on:
        if os.path.exists(AUTOSTART_FILE):
            os.remove(AUTOSTART_FILE)
        return
    os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
    with open(AUTOSTART_FILE, "w") as f:
        f.write(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Linux Nvidia Hardware Colour Controls\n"
            "Comment=Apply NVIDIA hardware brightness, contrast, gamma and digital vibrance\n"
            f"Exec={_exec_line()}\n"
            f"Icon={APP_ID}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            "X-KDE-autostart-phase=2\n"
        )
