"""Digital Vibrance through the ``nvkms-vibrance`` helper (NVKMS ioctls)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

HELPER_NAME = "nvkms-vibrance"


class NvkmsError(RuntimeError):
    pass


@dataclass
class Port:
    disp: int
    dpy_id: int
    type: str
    name: str
    connected: bool
    head: int | None
    vibrance: int | None
    edid: bytes


def helper_path() -> str | None:
    env = os.environ.get("LINUXNVIDIAHWCC_HELPER")
    if env and os.access(env, os.X_OK):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(here, HELPER_NAME),  # installed next to the package
        os.path.join(os.path.dirname(here), "helper", HELPER_NAME),  # source tree
    ):
        if os.access(cand, os.X_OK):
            return cand
    return shutil.which(HELPER_NAME)


def _run(*args: str) -> dict:
    path = helper_path()
    if not path:
        raise NvkmsError(f"{HELPER_NAME} helper not found; run 'make -C helper' or install.sh")
    try:
        p = subprocess.run([path, *args], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired as e:
        raise NvkmsError(f"{HELPER_NAME} timed out") from e
    if p.returncode != 0 and not p.stdout.strip():
        raise NvkmsError(p.stderr.strip() or f"{HELPER_NAME} exited with {p.returncode}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise NvkmsError(f"unexpected helper output: {p.stdout!r} {p.stderr!r}") from e


def list_ports() -> list[Port]:
    data = _run("list")
    return [
        Port(p["disp"], p["dpyId"], p["type"], p["name"], p["connected"], p.get("head"),
             p.get("vibrance"), bytes.fromhex(p.get("edid", "")))
        for p in data.get("ports", [])
    ]


def get_vibrance(disp: int, dpy_id: int) -> int | None:
    return _run("get", str(disp), str(dpy_id)).get("vibrance")


def set_vibrance(disp: int, dpy_id: int, value: int) -> int | None:
    return _run("set", str(disp), str(dpy_id), str(int(value))).get("vibrance")
