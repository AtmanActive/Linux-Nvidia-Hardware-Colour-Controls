#!/usr/bin/env bash
# Refresh the vendored NVKMS headers to match the running NVIDIA driver and
# rebuild the helper. Only needed if a driver update changes the NVKMS ABI
# (the helper then reports an ALLOC_DEVICE / ioctl failure).
#   helper/update-headers.sh [driver-version]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${1:-$(cat /sys/module/nvidia/version)}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Fetching NVKMS headers for driver $VERSION ..."
git clone -q --depth 1 --branch "$VERSION" --filter=blob:none --sparse \
    https://github.com/NVIDIA/open-gpu-kernel-modules "$TMP/ogkm"
git -C "$TMP/ogkm" sparse-checkout set \
    src/common/inc src/common/sdk/nvidia/inc src/common/unix/common/inc src/nvidia-modeset/interface

S="$TMP/ogkm/src"
for f in \
    common/inc/nv_mig_types.h \
    common/sdk/nvidia/inc/cpuopsys.h common/sdk/nvidia/inc/nvgputypes.h \
    common/sdk/nvidia/inc/nvlimits.h common/sdk/nvidia/inc/nvmisc.h common/sdk/nvidia/inc/nvtypes.h \
    common/unix/common/inc/nv_common_utils.h common/unix/common/inc/nv_dpy_id.h \
    common/unix/common/inc/nv_mode_timings.h \
    nvidia-modeset/interface/nvkms-api.h nvidia-modeset/interface/nvkms-api-types.h \
    nvidia-modeset/interface/nvkms-format.h nvidia-modeset/interface/nvkms-ioctl.h
do
    cp "$S/$f" "$HERE/nvidia-headers/"
done
cp "$TMP/ogkm/COPYING" "$HERE/nvidia-headers/COPYING"
echo "$VERSION" > "$HERE/nvidia-headers/VERSION"

make -C "$HERE" -B
"$HERE/nvkms-vibrance" list >/dev/null && echo "Helper OK for driver $VERSION. Re-run ./install.sh to install it."
