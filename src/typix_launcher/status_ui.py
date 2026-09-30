"""Compact GTK status strip; workers never touch GTK or block app launch."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

from gi.repository import GLib, Gtk

from .status import BATTERY_UNKNOWN, WIFI_UNKNOWN, battery_indicator, read_wifi, system_battery


class StatusStrip(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.closed = False
        self.active = False
        self.busy = False
        self.worker_lock = threading.Lock()
        self.child = None
        self.sampled_at = time.monotonic()
        self.items = []
        for value in (BATTERY_UNKNOWN, WIFI_UNKNOWN):
            icon = Gtk.Image.new_from_icon_name(value.icon, Gtk.IconSize.LARGE_TOOLBAR)
            icon.set_pixel_size(26)
            icon.set_valign(Gtk.Align.CENTER)
            icon.get_style_context().add_class('status-icon')
            self.items.append(icon)
            self.pack_start(icon, False, False, 8)
        self.apply(BATTERY_UNKNOWN, WIFI_UNKNOWN)
        self.timer = GLib.timeout_add_seconds(10, self.refresh)
        self.refresh()

    def apply(self, battery, wifi):
        if self.closed:
            return GLib.SOURCE_REMOVE
        for icon, value in zip(self.items, (battery, wifi)):
            icon.set_from_icon_name(value.icon, Gtk.IconSize.LARGE_TOOLBAR)
            icon.set_tooltip_text(value.detail)
            icon.get_accessible().set_name(value.detail)
        return GLib.SOURCE_REMOVE

    def refresh(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        if not self.active:
            return GLib.SOURCE_CONTINUE
        if self.busy:
            # A wedged provider must never leave an old percentage looking live.
            if time.monotonic() - self.sampled_at > 25:
                self.apply(BATTERY_UNKNOWN, WIFI_UNKNOWN)
            return GLib.SOURCE_CONTINUE
        self.busy = True
        threading.Thread(target=self.collect, daemon=True, name='launcher-status').start()
        return GLib.SOURCE_CONTINUE

    def set_active(self, active):
        self.active = active
        if active:
            self.refresh()
        else:
            with self.worker_lock:
                if self.child is not None:
                    self.child.kill()

    def collect(self):
        battery, wifi = BATTERY_UNKNOWN, WIFI_UNKNOWN
        try:
            battery = system_battery()
            if battery is None:
                battery = BATTERY_UNKNOWN
                with self.worker_lock:
                    if self.closed or not self.active:
                        return
                    child = self.child = subprocess.Popen(
                        [sys.executable, '-m', 'typix_launcher.battery'],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    )
                try:
                    stdout, _ = child.communicate(timeout=4)
                    sample = json.loads(stdout)
                    if (isinstance(sample, dict) and sample.get('source') == 'CW2015'
                            and type(sample.get('percent')) is int and 0 <= sample['percent'] <= 100
                            and type(sample.get('mv')) is int and 2500 <= sample['mv'] <= 4500):
                        battery = battery_indicator(sample['percent'], estimated=True, source='CW2015', mv=sample['mv'])
                except (ValueError, subprocess.TimeoutExpired):
                    child.kill()
                    child.communicate()
                finally:
                    with self.worker_lock:
                        self.child = None
            if self.closed:
                return
            wifi = read_wifi()
        except (OSError, ValueError):
            pass
        finally:
            self.sampled_at = time.monotonic()
            self.busy = False
            if not self.closed:
                GLib.idle_add(self.apply, battery or BATTERY_UNKNOWN, wifi)

    def close(self):
        self.closed = True
        GLib.source_remove(self.timer)
        # Synchronously release CDC and the writer lock before the supervisor
        # starts an app such as Copilot. No background serial service remains.
        with self.worker_lock:
            if self.child is not None:
                self.child.kill()
                self.child.wait(timeout=2)
