# Open-MXO2-Mini: Matrox MXO2 Mini driver, DKMS package for Fedora / RHEL-likes.
# Build: sh build-rpm.sh  (needs rpm-build)
%global modname mxo2
%global debug_package %{nil}

Name:           open-mxo2-mini-dkms
Version:        0.1
Release:        1%{?dist}
Summary:        Matrox MXO2 Mini capture box driver (DKMS)
License:        GPL-2.0-only
URL:            https://github.com/TomTheDragon/Open-MXO2-Mini
Source0:        open-mxo2-mini-%{version}.tar.gz
BuildArch:      noarch

Requires:       dkms
Requires:       kernel-devel
Requires(post): dkms
Requires(preun): dkms

%description
Unofficial, reverse-engineered kernel driver for the Matrox MXO2 Mini with its
PCIe card (102b:8350): HDMI and composite video capture (V4L2, two devices) and
audio (ALSA). Built with DKMS for every installed kernel. Includes a WirePlumber
rule that gives the two cameras distinct PipeWire names.

%prep
%setup -q -n open-mxo2-mini-%{version}

%build

%install
mkdir -p %{buildroot}%{_usrsrc}/%{modname}-%{version}
cp -a *.c *.h Makefile dkms.conf %{buildroot}%{_usrsrc}/%{modname}-%{version}/
install -D -m 644 51-mxo2.conf %{buildroot}%{_datadir}/wireplumber/wireplumber.conf.d/51-mxo2.conf

%post
dkms add -m %{modname} -v %{version} -q --rpm_safe_upgrade || :
dkms install -m %{modname} -v %{version} -q --rpm_safe_upgrade || :
modprobe %{modname} 2>/dev/null || :

%preun
rmmod %{modname} 2>/dev/null || :
dkms remove -m %{modname} -v %{version} -q --all --rpm_safe_upgrade || :

%files
%license LICENSE
%doc README.md
%{_usrsrc}/%{modname}-%{version}
%{_datadir}/wireplumber/wireplumber.conf.d/51-mxo2.conf

%changelog
* Thu Nov 05 2026 Open-MXO2-Mini <maintainer@example.invalid> - 0.1-1
- First package: HDMI + composite capture, analog/HDMI audio, two V4L2 devices
