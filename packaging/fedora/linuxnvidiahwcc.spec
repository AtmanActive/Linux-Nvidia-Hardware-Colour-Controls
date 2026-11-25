# Fedora package. Built by packaging/build-package.sh, which passes
# --define "pkgver X.Y.Z" and --define "pkgrel N".
%{!?pkgver: %global pkgver 1.0.0}
%{!?pkgrel: %global pkgrel 1}
# Single small C helper; no separate debuginfo package.
%global debug_package %{nil}
# No %%changelog is kept here; release notes live on GitHub.
%global source_date_epoch_from_changelog 0

Name:           linuxnvidiahwcc
Version:        %{pkgver}
Release:        %{pkgrel}%{?dist}
Summary:        NVIDIA hardware brightness, contrast, gamma and vibrance controls for KDE Plasma

License:        MIT
URL:            https://github.com/AtmanActive/Linux-Nvidia-Hardware-Colour-Controls
Source0:        %{name}-%{version}.tar.gz
ExclusiveArch:  x86_64

BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  desktop-file-utils
Requires:       python3
Requires:       python3-pyside6
Requires:       libkscreen
Requires:       systemd

%description
NVIDIA Control Panel style Brightness, Contrast, Gamma and Digital Vibrance for
KDE Plasma 6 on Wayland, applied only in the GPU's display hardware: the CRTC
gamma LUT (through KWin) and the NVIDIA display engine's digital vibrance.
No software, shader or per-application colour correction.

%prep
%autosetup

%build
%make_build helper

%install
%make_install PREFIX=%{_prefix}

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{name}.desktop

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}
%{_prefix}/lib/%{name}/
%{_datadir}/applications/%{name}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
