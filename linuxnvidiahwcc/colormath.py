"""Colour maths: NVIDIA-style brightness/contrast/gamma ramp and vibrance mapping.

The ramp reproduces the formula NVIDIA uses for its desktop colour
controls (see nvidia-settings, ``ComputeGammaRampVal`` in
src/libXNVCtrlAttributes/NvCtrlAttributes.c), expressed on a normalised
0..1 scale so it can be sampled at any LUT size.
"""

from __future__ import annotations

import math

Matrix = list[list[float]]

# UI ranges, matching the NVIDIA Control Panel on Windows.
BRIGHTNESS_RANGE = (0, 100)  # percent, 50 = neutral
CONTRAST_RANGE = (0, 100)  # percent, 50 = neutral
GAMMA_RANGE = (0.30, 2.80)  # 1.00 = neutral
VIBRANCE_RANGE = (0, 100)  # percent, 50 = neutral

DEFAULTS = {
    "brightness": 50,
    "contrast": 50,
    "gamma": 1.0,
    "vibrance": 50,
}


def nv_ramp_value(x: float, brightness: float, contrast: float, gamma: float) -> float:
    """One ramp sample, NVIDIA formula.

    x          input in 0..1
    brightness -1..1 (0 neutral)
    contrast   -1..1 (0 neutral)
    gamma      >0    (1 neutral, >1 brightens mid-tones)
    """
    scale = 1.0 / 3.0  # how far brightness/contrast can move the curve
    c = contrast * scale
    if c > 0.0:
        x = (x - 0.5) * (0.5 / (0.5 - c)) + 0.5
    else:
        x = (x - 0.5) * ((0.5 + c) / 0.5) + 0.5
    x += brightness * scale
    x = min(1.0, max(0.0, x))
    if gamma != 1.0:
        x = x ** (1.0 / gamma)
    return x


def ramp(size: int, brightness_pct: float, contrast_pct: float, gamma: float) -> list[float]:
    """Sample the ramp using Control-Panel style values (percent, percent, gamma)."""
    b = (brightness_pct - 50.0) / 50.0
    c = (contrast_pct - 50.0) / 50.0
    return [nv_ramp_value(i / (size - 1), b, c, gamma) for i in range(size)]


def vibrance_to_dvc(pct: float) -> int:
    """Map Control-Panel vibrance (0..100 %, 50 neutral) to NVKMS DVC (-1024..1023)."""
    v = round((pct - 50.0) / 50.0 * 1024)
    return max(-1024, min(1023, v))


# --- small 3x3 helpers -------------------------------------------------------

def matmul(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def matinv(m: Matrix) -> Matrix:
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]


def xy_to_xyz(x: float, y: float) -> list[float]:
    return [x / y, 1.0, (1.0 - x - y) / y]


def rgb_to_xyz_matrix(r, g, b, w) -> Matrix:
    """RGB->XYZ matrix from xy chromaticities of the primaries and white."""
    cols = [xy_to_xyz(*p) for p in (r, g, b)]
    m = [[cols[j][i] for j in range(3)] for i in range(3)]
    wv = xy_to_xyz(*w)
    inv = matinv(m)
    s = [sum(inv[i][k] * wv[k] for k in range(3)) for i in range(3)]
    return [[m[i][j] * s[j] for j in range(3)] for i in range(3)]


BT709_R = (0.640, 0.330)
BT709_G = (0.300, 0.600)
BT709_B = (0.150, 0.060)
D65 = (0.3127, 0.3290)
D50_XYZ = [0.9642, 1.0, 0.8249]

BT709_TO_XYZ = rgb_to_xyz_matrix(BT709_R, BT709_G, BT709_B, D65)

BRADFORD = [
    [0.8951, 0.2664, -0.1614],
    [-0.7502, 1.7135, 0.0367],
    [0.0389, -0.0685, 1.0296],
]


def bradford_adaptation(src_white_xyz, dst_white_xyz) -> Matrix:
    ms = [sum(BRADFORD[i][k] * src_white_xyz[k] for k in range(3)) for i in range(3)]
    md = [sum(BRADFORD[i][k] * dst_white_xyz[k] for k in range(3)) for i in range(3)]
    scale = [[md[i] / ms[i] if i == j else 0.0 for j in range(3)] for i in range(3)]
    return matmul(matinv(BRADFORD), matmul(scale, BRADFORD))
