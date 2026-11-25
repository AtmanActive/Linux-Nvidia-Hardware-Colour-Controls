"""Minimal ICC v4 display profile writer.

The profile describes an exact sRGB/BT.709 display with a gamma 2.2 tone
curve (so KWin's compositing is left untouched) and carries the
Brightness/Contrast/Gamma ramp twice, for different KWin versions:

* ``MHC2`` (Microsoft's hardware calibration tag): identity colour matrix plus
  per-channel 1D LUTs. Used by KWin >= 6.4-ish (verified on 6.8), which lets
  it take precedence over ``vcgt``.
* ``vcgt`` (video card gamma table): the same per-channel LUTs. Used by older
  KWin without MHC2 support (e.g. 6.3 in Debian 13).

When KWin's colour power preference is "prefer efficiency", KWin programs the
LUT into the CRTC ``GAMMA_LUT``: the display engine's hardware gamma ramp,
the same place the Windows NVIDIA Control Panel writes its ramp.
"""

from __future__ import annotations

import struct
import time

from . import colormath as cm


def _s15(x: float) -> bytes:
    return struct.pack(">i", int(round(x * 65536)))


def _u16f16(x: float) -> bytes:
    return struct.pack(">I", int(round(x * 65536)))


def _pad4(b: bytes) -> bytes:
    return b + b"\0" * ((-len(b)) % 4)


def _mluc(text: str) -> bytes:
    s = text.encode("utf-16-be")
    return b"mluc" + b"\0" * 4 + struct.pack(">II", 1, 12) + b"enUS" + struct.pack(">II", len(s), 28) + s


def _xyz(v) -> bytes:
    return b"XYZ " + b"\0" * 4 + b"".join(_s15(x) for x in v)


def _sf32(values) -> bytes:
    return b"sf32" + b"\0" * 4 + b"".join(_s15(x) for x in values)


def _para_gamma(g: float) -> bytes:
    # parametricCurveType, function type 0: Y = X^g
    return b"para" + b"\0" * 4 + struct.pack(">HH", 0, 0) + _s15(g)


def _chrm() -> bytes:
    body = b"chrm" + b"\0" * 4 + struct.pack(">HH", 3, 1)  # 3 channels, ITU-R BT.709
    for x, y in (cm.BT709_R, cm.BT709_G, cm.BT709_B):
        body += _u16f16(x) + _u16f16(y)
    return body


def _vcgt(lut_r, lut_g, lut_b) -> bytes:
    # Apple 'vcgt' tag, table form: type 0, 3 channels, n entries of 2 bytes each.
    n = len(lut_r)
    body = b"vcgt" + b"\0" * 4 + struct.pack(">IHHH", 0, 3, n, 2)
    for lut in (lut_r, lut_g, lut_b):
        body += b"".join(struct.pack(">H", int(round(min(max(v, 0.0), 1.0) * 65535))) for v in lut)
    return body


def _mhc2(matrix_xyz, lut_r, lut_g, lut_b) -> bytes:
    n = len(lut_r)
    header_len = 36
    matrix_off = header_len
    matrix = b"".join(
        _s15(matrix_xyz[r][c]) if c < 3 else _s15(0.0) for r in range(3) for c in range(4)
    )
    lut_len = 8 + 4 * n
    r_off = matrix_off + len(matrix)
    g_off = r_off + lut_len
    b_off = g_off + lut_len
    head = b"MHC2" + b"\0" * 4 + struct.pack(">I", n)
    head += _s15(0.0) + _s15(0.0)  # min / peak luminance: 0 = "not specified", KWin ignores
    head += struct.pack(">IIII", matrix_off, r_off, g_off, b_off)
    return head + matrix + _sf32(lut_r) + _sf32(lut_g) + _sf32(lut_b)


def build_profile(
    brightness: float = 50,
    contrast: float = 50,
    gamma: float = 1.0,
    lut_size: int = 1024,
    description: str | None = None,
) -> bytes:
    """Return the bytes of an ICC profile encoding the given adjustments."""
    lut = cm.ramp(lut_size, brightness, contrast, gamma)

    # Identity: any non-identity matrix would make KWin fall back to a shader on NVIDIA.
    m_xyz = [[1.0 if r == c else 0.0 for c in range(3)] for r in range(3)]

    d65_xyz = cm.xy_to_xyz(*cm.D65)
    chad = cm.bradford_adaptation(d65_xyz, cm.D50_XYZ)
    colorants = cm.matmul(chad, cm.BT709_TO_XYZ)  # D50-adapted colourants (ICC convention)

    if description is None:
        description = (
            f"linuxnvidiahwcc B{brightness:.0f} C{contrast:.0f} G{gamma:.2f}"
        )
    trc = _para_gamma(2.2)
    tags: list[tuple[bytes, bytes | None]] = [
        (b"desc", _mluc(description)),
        (b"cprt", _mluc("No copyright, use freely")),
        (b"wtpt", _xyz(cm.D50_XYZ)),
        (b"chad", b"sf32" + b"\0" * 4 + b"".join(_s15(v) for row in chad for v in row)),
        (b"chrm", _chrm()),
        (b"rXYZ", _xyz([colorants[i][0] for i in range(3)])),
        (b"gXYZ", _xyz([colorants[i][1] for i in range(3)])),
        (b"bXYZ", _xyz([colorants[i][2] for i in range(3)])),
        (b"rTRC", trc),
        (b"gTRC", None),  # shares rTRC data
        (b"bTRC", None),
        (b"MHC2", _mhc2(m_xyz, lut, lut, lut)),
        (b"vcgt", _vcgt(lut, lut, lut)),
    ]

    table_len = 4 + 12 * len(tags)
    offset = 128 + table_len
    body = b""
    table = b""
    trc_entry = None
    for sig, data in tags:
        if data is None:
            table += sig + trc_entry
            continue
        entry = struct.pack(">II", offset + len(body), len(data))
        if sig == b"rTRC":
            trc_entry = entry
        table += sig + entry
        body += _pad4(data)

    size = 128 + table_len + len(body)
    t = time.gmtime()
    header = struct.pack(">I", size)
    header += b"\0\0\0\0"  # preferred CMM
    header += struct.pack(">I", 0x04300000)  # version 4.3
    header += b"mntr" + b"RGB " + b"XYZ "
    header += struct.pack(">6H", t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
    header += b"acsp" + b"\0" * 4  # platform
    header += b"\0" * 4  # flags
    header += b"\0" * 8  # manufacturer, model
    header += b"\0" * 8  # attributes
    header += struct.pack(">I", 0)  # rendering intent: perceptual
    header += b"".join(_s15(v) for v in cm.D50_XYZ)  # PCS illuminant
    header += b"\0" * 4  # creator
    header = header.ljust(128, b"\0")
    return header + struct.pack(">I", len(tags)) + table + body


def write_profile(path: str, **kwargs) -> None:
    data = build_profile(**kwargs)
    with open(path, "wb") as f:
        f.write(data)
