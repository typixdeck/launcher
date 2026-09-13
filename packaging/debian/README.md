# Debian packaging target

This directory will contain the launcher's own `debian/` packaging source.

Requirements:

- Package name: `typix-launcher`.
- Architecture: `all` for Python/UI, with strict `arm64` runtime dependency checks.
- Depends at runtime: `python3`, `python3-gi`, `gir1.2-gtk-3.0`.
- Install UI under `/usr/share/typix-launcher/` and entry point under `/usr/bin/`.
- Install systemd user unit and Labwc integration only through declarative dpkg paths.
- Include trusted registry public keys.
- Do not create or modify the `pi` user in maintainer scripts.
- No sudoers rule in the new package; privileged store actions use polkit.
