# Source layout target

This package starts empty by design until M1 extracts tested behavior from the reference launcher.

Planned modules:

- `desktop.py`: FreeDesktop discovery, localization, validation, and deduplication.
- `supervisor.py`: launch request state machine and child process lifecycle.
- `device.py`: read-only capability probing and UI profile selection.
- `store/registry.py`: signed catalog download, cache, and schema validation.
- `store/installer.py`: download queue and helper integration.
- `ui/`: GTK3 pages, widgets, and CSS loading.
