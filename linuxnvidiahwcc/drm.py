"""Read-only access to DRM/KMS state of the NVIDIA card.

Used to (a) map DRM connector names (as KDE shows them, e.g. ``HDMI-A-2``)
to CRTC indices, which equal NVIDIA display heads, and (b) read back the
CRTC's GAMMA_LUT / CTM / DEGAMMA_LUT so the app can verify the adjustment
is really programmed into the display hardware.

Only GET ioctls are used; they work without being DRM master, so nothing
here can disturb the compositor.
"""

from __future__ import annotations

import ctypes
import fcntl
import os
import struct
from dataclasses import dataclass, field

# --- ioctl number helpers (asm-generic) --------------------------------------
_IOC_WRITE, _IOC_READ = 1, 2


def _IOWR(nr: int, size: int) -> int:
    return ((_IOC_READ | _IOC_WRITE) << 30) | (size << 16) | (ord("d") << 8) | nr


class _CardRes(ctypes.Structure):
    _fields_ = [
        ("fb_id_ptr", ctypes.c_uint64),
        ("crtc_id_ptr", ctypes.c_uint64),
        ("connector_id_ptr", ctypes.c_uint64),
        ("encoder_id_ptr", ctypes.c_uint64),
        ("count_fbs", ctypes.c_uint32),
        ("count_crtcs", ctypes.c_uint32),
        ("count_connectors", ctypes.c_uint32),
        ("count_encoders", ctypes.c_uint32),
        ("min_width", ctypes.c_uint32),
        ("max_width", ctypes.c_uint32),
        ("min_height", ctypes.c_uint32),
        ("max_height", ctypes.c_uint32),
    ]


class _GetConnector(ctypes.Structure):
    _fields_ = [
        ("encoders_ptr", ctypes.c_uint64),
        ("modes_ptr", ctypes.c_uint64),
        ("props_ptr", ctypes.c_uint64),
        ("prop_values_ptr", ctypes.c_uint64),
        ("count_modes", ctypes.c_uint32),
        ("count_props", ctypes.c_uint32),
        ("count_encoders", ctypes.c_uint32),
        ("encoder_id", ctypes.c_uint32),
        ("connector_id", ctypes.c_uint32),
        ("connector_type", ctypes.c_uint32),
        ("connector_type_id", ctypes.c_uint32),
        ("connection", ctypes.c_uint32),
        ("mm_width", ctypes.c_uint32),
        ("mm_height", ctypes.c_uint32),
        ("subpixel", ctypes.c_uint32),
        ("pad", ctypes.c_uint32),
    ]


class _GetEncoder(ctypes.Structure):
    _fields_ = [
        ("encoder_id", ctypes.c_uint32),
        ("encoder_type", ctypes.c_uint32),
        ("crtc_id", ctypes.c_uint32),
        ("possible_crtcs", ctypes.c_uint32),
        ("possible_clones", ctypes.c_uint32),
    ]


class _ObjGetProps(ctypes.Structure):
    _fields_ = [
        ("props_ptr", ctypes.c_uint64),
        ("prop_values_ptr", ctypes.c_uint64),
        ("count_props", ctypes.c_uint32),
        ("obj_id", ctypes.c_uint32),
        ("obj_type", ctypes.c_uint32),
    ]


class _GetProperty(ctypes.Structure):
    _fields_ = [
        ("values_ptr", ctypes.c_uint64),
        ("enum_blob_ptr", ctypes.c_uint64),
        ("prop_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
        ("count_values", ctypes.c_uint32),
        ("count_enum_blobs", ctypes.c_uint32),
    ]


class _GetBlob(ctypes.Structure):
    _fields_ = [
        ("blob_id", ctypes.c_uint32),
        ("length", ctypes.c_uint32),
        ("data", ctypes.c_uint64),
    ]


GETRESOURCES = _IOWR(0xA0, ctypes.sizeof(_CardRes))
GETENCODER = _IOWR(0xA6, ctypes.sizeof(_GetEncoder))
GETCONNECTOR = _IOWR(0xA7, ctypes.sizeof(_GetConnector))
GETPROPERTY = _IOWR(0xAA, ctypes.sizeof(_GetProperty))
GETPROPBLOB = _IOWR(0xAC, ctypes.sizeof(_GetBlob))
OBJ_GETPROPERTIES = _IOWR(0xB9, ctypes.sizeof(_ObjGetProps))

OBJ_CRTC = 0xCCCCCCCC

CONNECTOR_TYPES = {
    0: "Unknown", 1: "VGA", 2: "DVI-I", 3: "DVI-D", 4: "DVI-A", 5: "Composite",
    6: "SVIDEO", 7: "LVDS", 8: "Component", 9: "DIN", 10: "DP", 11: "HDMI-A",
    12: "HDMI-B", 13: "TV", 14: "eDP", 15: "Virtual", 16: "DSI", 17: "DPI",
    18: "Writeback", 19: "SPI", 20: "USB",
}


@dataclass
class Connector:
    name: str
    connected: bool
    crtc_id: int | None
    crtc_index: int | None


@dataclass
class CrtcColor:
    gamma_lut: list[tuple[float, float, float]] | None = None
    degamma_lut_set: bool = False
    ctm: list[float] | None = None
    extra: dict = field(default_factory=dict)


def find_nvidia_card() -> str | None:
    """Return /dev/dri/cardN of the first NVIDIA KMS device."""
    base = "/sys/class/drm"
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return None
    for name in entries:
        if not name.startswith("card") or "-" in name:
            continue
        try:
            with open(f"{base}/{name}/device/vendor") as f:
                if f.read().strip() == "0x10de":
                    return f"/dev/dri/{name}"
        except OSError:
            continue
    return None


def _ioctl(fd: int, req: int, obj: ctypes.Structure) -> None:
    fcntl.ioctl(fd, req, obj, True)


def _u32array(n: int):
    return (ctypes.c_uint32 * max(n, 1))()


class DrmCard:
    def __init__(self, path: str):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)

    def close(self) -> None:
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _resources(self):
        res = _CardRes()
        _ioctl(self.fd, GETRESOURCES, res)
        crtcs, conns = _u32array(res.count_crtcs), _u32array(res.count_connectors)
        fbs, encs = _u32array(res.count_fbs), _u32array(res.count_encoders)
        res.crtc_id_ptr = ctypes.addressof(crtcs)
        res.connector_id_ptr = ctypes.addressof(conns)
        res.fb_id_ptr = ctypes.addressof(fbs)
        res.encoder_id_ptr = ctypes.addressof(encs)
        _ioctl(self.fd, GETRESOURCES, res)
        return list(crtcs[: res.count_crtcs]), list(conns[: res.count_connectors])

    def connectors(self) -> list[Connector]:
        crtc_ids, conn_ids = self._resources()
        out = []
        for cid in conn_ids:
            c = _GetConnector(connector_id=cid)
            _ioctl(self.fd, GETCONNECTOR, c)  # count_* = 0: no probe, no allocation
            name = f"{CONNECTOR_TYPES.get(c.connector_type, 'Unknown')}-{c.connector_type_id}"
            crtc_id = None
            if c.encoder_id:
                e = _GetEncoder(encoder_id=c.encoder_id)
                try:
                    _ioctl(self.fd, GETENCODER, e)
                    crtc_id = e.crtc_id or None
                except OSError:
                    pass
            idx = crtc_ids.index(crtc_id) if crtc_id in crtc_ids else None
            out.append(Connector(name, c.connection == 1, crtc_id, idx))
        return out

    def _props(self, obj_id: int, obj_type: int) -> dict[str, int]:
        p = _ObjGetProps(obj_id=obj_id, obj_type=obj_type)
        _ioctl(self.fd, OBJ_GETPROPERTIES, p)
        n = p.count_props
        ids, vals = _u32array(n), (ctypes.c_uint64 * max(n, 1))()
        p.props_ptr, p.prop_values_ptr = ctypes.addressof(ids), ctypes.addressof(vals)
        _ioctl(self.fd, OBJ_GETPROPERTIES, p)
        out = {}
        for i in range(min(n, p.count_props)):
            gp = _GetProperty(prop_id=ids[i])
            _ioctl(self.fd, GETPROPERTY, gp)
            out[gp.name.decode()] = vals[i]
        return out

    def _blob(self, blob_id: int) -> bytes | None:
        if not blob_id:
            return None
        b = _GetBlob(blob_id=blob_id)
        _ioctl(self.fd, GETPROPBLOB, b)
        buf = ctypes.create_string_buffer(b.length)
        b.data = ctypes.addressof(buf)
        _ioctl(self.fd, GETPROPBLOB, b)
        return buf.raw[: b.length]

    def crtc_color(self, crtc_id: int) -> CrtcColor:
        props = self._props(crtc_id, OBJ_CRTC)
        cc = CrtcColor()
        lut = self._blob(props.get("GAMMA_LUT", 0))
        if lut:
            n = len(lut) // 8
            cc.gamma_lut = [
                tuple(v / 65535 for v in struct.unpack_from("<3H", lut, i * 8)) for i in range(n)
            ]
        cc.degamma_lut_set = bool(props.get("DEGAMMA_LUT", 0))
        ctm = self._blob(props.get("CTM", 0))
        if ctm:
            vals = []
            for (u,) in struct.iter_unpack("<Q", ctm):
                mag = (u & ((1 << 63) - 1)) / 2**32
                vals.append(-mag if u >> 63 else mag)
            cc.ctm = vals
        return cc


def card_of_connector(connector_name: str) -> str | None:
    """/dev/dri/cardN that owns a connector such as ``HDMI-A-2``."""
    base = "/sys/class/drm"
    try:
        for entry in os.listdir(base):
            card, sep, conn = entry.partition("-")
            if sep and conn == connector_name and card.startswith("card"):
                return f"/dev/dri/{card}"
    except OSError:
        pass
    return None


def is_nvidia_card(path: str) -> bool:
    try:
        with open(f"/sys/class/drm/{os.path.basename(path)}/device/vendor") as f:
            return f.read().strip() == "0x10de"
    except OSError:
        return False


def connector_map() -> dict[str, Connector]:
    """Connectors of the NVIDIA card, keyed by name."""
    path = find_nvidia_card()
    if not path:
        return {}
    with DrmCard(path) as card:
        return {c.name: c for c in card.connectors()}


def read_crtc_color(connector_name: str) -> CrtcColor | None:
    path = card_of_connector(connector_name)
    if not path:
        return None
    with DrmCard(path) as card:
        for c in card.connectors():
            if c.name == connector_name and c.crtc_id:
                return card.crtc_color(c.crtc_id)
    return None
