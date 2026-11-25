#!/usr/bin/env bash
# Build the native package for one distribution.
#
#   packaging/build-package.sh <fedora|debian|cachyos> [outdir]
#
# Run as root inside a container of the target distribution (the Release
# workflow uses fedora:latest, debian:stable and archlinux:latest). Installs
# the build dependencies, builds from a clean copy of the source and writes
# the package to outdir (default: dist/).
#
# Environment:
#   PKGREL   package release number (default 1); bump it to re-package the
#            same application version.
set -euo pipefail

NAME=linuxnvidiahwcc
TARGET=${1:?usage: build-package.sh <fedora|debian|cachyos> [outdir]}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "${2:-$ROOT/dist}"
OUT=$(cd "${2:-$ROOT/dist}" && pwd)
VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$ROOT/$NAME/__init__.py")
PKGREL=${PKGREL:-1}

die() { echo "error: $*" >&2; exit 1; }
[[ $VERSION =~ ^[0-9]+(\.[0-9]+)*$ ]] || die "could not read __version__ from $NAME/__init__.py"
[[ $PKGREL =~ ^[1-9][0-9]*$ ]] || die "PKGREL must be a positive integer, got '$PKGREL'"

WORK=$(mktemp -d)
chmod 755 "$WORK"  # makepkg runs as an unprivileged user
trap 'rm -rf "$WORK"' EXIT

# Source tarball of the tree, without build output or VCS data.
source_tarball() {
    tar -C "$ROOT" \
        --exclude=./.git --exclude=./dist --exclude=__pycache__ --exclude='*.pyc' \
        --exclude=./helper/nvkms-vibrance \
        --transform "s|^\.|$NAME-$VERSION|" -czf "$1" .
}

build_fedora() {
    dnf -y install gcc make rpm-build desktop-file-utils tar gzip
    mkdir -p "$WORK"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
    source_tarball "$WORK/SOURCES/$NAME-$VERSION.tar.gz"
    rpmbuild -bb \
        --define "_topdir $WORK" \
        --define "pkgver $VERSION" \
        --define "pkgrel $PKGREL" \
        "$ROOT/packaging/fedora/$NAME.spec"
    find "$WORK/RPMS" -name "$NAME-*.rpm" -exec cp {} "$OUT/" \;
}

build_debian() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    apt-get install -y -q --no-install-recommends build-essential dpkg-dev
    source_tarball "$WORK/src.tar.gz"
    tar -xzf "$WORK/src.tar.gz" -C "$WORK"
    local src="$WORK/$NAME-$VERSION" stage="$WORK/stage"

    eval "$(dpkg-buildflags --export=sh)"  # Debian's hardening flags
    make -C "$src" DESTDIR="$stage" PREFIX=/usr install
    strip --strip-unneeded "$stage/usr/lib/$NAME/$NAME/nvkms-vibrance"
    install -Dm644 "$src/LICENSE" "$stage/usr/share/doc/$NAME/copyright"
    install -Dm644 "$src/README.md" "$stage/usr/share/doc/$NAME/README.md"

    # Shared-library dependencies of the helper, e.g. "libc6 (>= 2.34)".
    mkdir -p "$WORK/shlibs/debian"
    printf 'Source: %s\n\nPackage: %s\nArchitecture: amd64\n' "$NAME" "$NAME" > "$WORK/shlibs/debian/control"
    local shlibs
    shlibs=$(cd "$WORK/shlibs" && dpkg-shlibdeps -O "$stage/usr/lib/$NAME/$NAME/nvkms-vibrance" \
             | sed -n 's/^shlibs:Depends=//p')
    [[ -n $shlibs ]] || die "dpkg-shlibdeps found no dependencies"

    mkdir -p "$stage/DEBIAN"
    sed -e "s|@VERSION@|$VERSION|" -e "s|@PKGREL@|$PKGREL|" -e "s|@SHLIBS@|$shlibs|" \
        -e "s|@INSTALLED_SIZE@|$(du -sk "$stage" | cut -f1)|" \
        "$ROOT/packaging/debian/control.in" > "$stage/DEBIAN/control"
    dpkg-deb --root-owner-group --build "$stage" "$OUT/${NAME}_${VERSION}-${PKGREL}_amd64.deb"
}

build_arch() {
    pacman -Syu --noconfirm --needed base-devel
    id builder >/dev/null 2>&1 || useradd -m builder
    local b="$WORK/arch"
    mkdir -p "$b/out"
    sed -e "s/^pkgver=.*/pkgver=$VERSION/" -e "s/^pkgrel=.*/pkgrel=$PKGREL/" \
        "$ROOT/packaging/arch/PKGBUILD" > "$b/PKGBUILD"
    source_tarball "$b/$NAME-$VERSION.tar.gz"
    chown -R builder: "$b"
    # Runtime dependencies are not needed to build; base-devel covers makedepends.
    runuser -l builder -c "cd '$b' && PKGDEST='$b/out' makepkg --nodeps --noconfirm --clean"
    cp "$b"/out/"$NAME"-*.pkg.tar.zst "$OUT/"
}

case "$TARGET" in
    fedora) build_fedora ;;
    debian) build_debian ;;
    cachyos | arch) build_arch ;;
    *) die "unknown target '$TARGET' (expected fedora, debian or cachyos)" ;;
esac

echo "Built:"
ls -l "$OUT"
