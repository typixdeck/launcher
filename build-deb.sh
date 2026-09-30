#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION=${VERSION:-0.3.0-1}
STAGE="$ROOT/build/package"
DIST="$ROOT/dist"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/lib/python3/dist-packages" "$STAGE/usr/lib/systemd/user" "$STAGE/usr/share/typix-launcher" "$STAGE/usr/share/applications" "$STAGE/usr/share/doc/typix-launcher" "$DIST"
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: typix-launcher
Version: $VERSION
Architecture: all
Maintainer: TypixDeck <dev@typixnode.com>
Section: x11
Priority: optional
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-3.0, xdg-user-dirs
X-Typix-Compatible-OS: raspios-bookworm,raspios-trixie
Recommends: wlopm, network-manager
Description: TypixDeck full-screen launcher and app supervisor
 Native GTK3 launcher for CM0/CM4/CM5 devices. It discovers FreeDesktop
 entries and offers low-memory single-app or resident multitasking modes.
CONTROL
printf '%s\n' 'Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/' 'Upstream-Name: typix-launcher' > "$STAGE/usr/share/doc/typix-launcher/copyright"
cat > "$STAGE/usr/bin/typix-launcher" <<'RUNNER'
#!/bin/sh
set -eu
exec /usr/bin/python3 -m typix_launcher "$@"
RUNNER
chmod 755 "$STAGE/usr/bin/typix-launcher"
cp -R "$ROOT/src/typix_launcher" "$STAGE/usr/lib/python3/dist-packages/"
find "$STAGE/usr/lib/python3/dist-packages" -name '__pycache__' -type d -prune -exec rm -rf {} +
install -m 644 "$ROOT/src/typix_launcher/typix-launcher.css" "$STAGE/usr/share/typix-launcher/typix-launcher.css"
install -m 644 "$ROOT/config/systemd/typix-launcher.service" "$STAGE/usr/lib/systemd/user/typix-launcher.service"
install -m 644 "$ROOT/packaging/debian/typix-launcher.desktop" "$STAGE/usr/share/applications/typix-launcher.desktop"
install -d "$STAGE/usr/lib/tmpfiles.d"
install -m 644 "$ROOT/packaging/debian/typix-launcher.tmpfiles" "$STAGE/usr/lib/tmpfiles.d/typix-launcher.conf"
install -m 755 "$ROOT/packaging/debian/postinst" "$STAGE/DEBIAN/postinst"
dpkg-deb --root-owner-group --build "$STAGE" "$DIST/typix-launcher_${VERSION}_all.deb"
