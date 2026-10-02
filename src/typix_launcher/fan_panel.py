"""Settings-only fan controls; worker threads never touch GTK widgets."""
from __future__ import annotations

import threading

from gi.repository import GLib, Gtk

from .fan import FanState
from .fan_labels import readings_label


class FanPanel(Gtk.Box):
    def __init__(self, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.controller = controller
        self.closed = False
        self.timer = 0
        self.reading = False
        self.busy = False
        self.initialized = False
        self.generation = 0
        self.last_state = FanState()
        self.feedback = False
        self.set_border_width(18)
        self.connect("destroy", lambda _widget: self.close())

        header = Gtk.Box(spacing=12)
        title = Gtk.Label(label="风扇", xalign=0)
        title.set_hexpand(True)
        header.pack_start(title, True, True, 0)
        self.mode = Gtk.ComboBoxText()
        self.mode.set_name("fan-mode")
        for value, label in (("auto", "自动"), ("manual", "手动")):
            self.mode.append(value, label)
        self.mode.set_active_id("auto")
        self.mode.set_can_focus(True)
        self.mode.connect("changed", self.on_draft_changed)
        header.pack_end(self.mode, False, False, 0)
        self.pack_start(header, False, False, 0)

        self.readings = Gtk.Label(label="温度 —    转速 —    输出 —", xalign=0)
        self.readings.set_line_wrap(True)
        self.readings.set_max_width_chars(40)
        self.pack_start(self.readings, False, False, 0)
        self.hint = Gtk.Label(label="随温度调速 · 高温保护", xalign=0)
        self.hint.get_style_context().add_class("launcher-hint")
        self.pack_start(self.hint, False, False, 0)

        manual_row = self.manual_row = Gtk.Box(spacing=12)
        manual_row.set_no_show_all(True)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 30, 100, 5)
        self.scale.set_name("fan-percent")
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.set_can_focus(True)
        self.scale.set_value(50)
        self.scale.connect("value-changed", self.on_draft_changed)
        self.percent = Gtk.Label(label="50%")
        self.percent.set_width_chars(5)
        manual_row.pack_start(self.scale, True, True, 0)
        manual_row.pack_end(self.percent, False, False, 0)
        manual_row.show_all()
        manual_row.hide()
        self.pack_start(manual_row, False, False, 0)

        action_row = Gtk.Box(spacing=12)
        self.message = Gtk.Label(label="正在读取风扇状态…", xalign=0)
        self.message.set_hexpand(True)
        self.message.set_line_wrap(True)
        self.message.set_max_width_chars(32)
        self.message.get_style_context().add_class("launcher-hint")
        self.apply_button = Gtk.Button(label="应用")
        self.apply_button.set_name("fan-apply")
        self.apply_button.set_can_focus(True)
        self.apply_button.connect("clicked", self.on_apply)
        action_row.pack_start(self.message, True, True, 0)
        action_row.pack_end(self.apply_button, False, False, 0)
        self.pack_start(action_row, False, False, 0)
        self.update_controls()

    def start(self):
        if self.closed or self.timer:
            return
        self.timer = GLib.timeout_add(2500, self.refresh)
        self.refresh()

    def close(self):
        self.closed = True
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        # An authorized system transaction belongs to the helper. Closing the
        # settings only discards its eventual UI callback; it never kills it.

    def update_controls(self):
        enabled = self.last_state.available and not self.busy and not self.closed
        self.mode.set_sensitive(enabled)
        self.scale.set_sensitive(enabled)
        self.apply_button.set_sensitive(enabled)
        manual = self.mode.get_active_id() == "manual"
        self.manual_row.set_visible(manual)
        self.hint.set_text("手动速度 · 高温时自动保护" if manual else "随温度调速 · 高温保护")

    def on_draft_changed(self, _widget):
        self.percent.set_text(f"{int(round(self.scale.get_value()))}%")
        if self.initialized and not self.busy:
            self.feedback = False
            self.message.set_text("点击应用保存")
        self.update_controls()

    def refresh(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        if self.busy or self.reading:
            return GLib.SOURCE_CONTINUE
        self.reading = True
        generation = self.generation

        def collect():
            try:
                state = self.controller.state()
            except Exception:
                state = FanState(error="read", message="无法读取风扇状态")
            GLib.idle_add(self.finish_read, state, generation)

        threading.Thread(target=collect, daemon=True, name="launcher-fan-read").start()
        return GLib.SOURCE_CONTINUE

    def finish_read(self, state, generation):
        self.reading = False
        if self.closed or generation != self.generation:
            return GLib.SOURCE_REMOVE
        self.render_state(state)
        return GLib.SOURCE_REMOVE

    def render_state(self, state, *, result=False):
        self.last_state = state
        if not self.initialized:
            self.mode.set_active_id(state.mode if state.mode in ("auto", "manual") else "auto")
            self.scale.set_value(max(30, min(100, state.requested_percent)))
            self.initialized = True
        self.readings.set_text(readings_label(state))
        if result or not self.feedback or state.error or not state.available or state.mode == "off":
            if state.error or not state.available or state.mode == "off":
                self.feedback = False
            self.message.set_text(state.message or (
                "需启用 EMC2301 驱动" if not state.available else
                "尚未启用风扇控制" if state.mode == "off" else ""))
        self.update_controls()

    def on_apply(self, _button):
        if self.closed or self.busy or not self.last_state.available:
            return
        mode = self.mode.get_active_id()
        percent = int(round(self.scale.get_value()))
        self.busy = True
        self.generation += 1
        self.message.set_text("正在应用，请完成系统授权…")
        self.apply_button.set_label("应用中…")
        self.update_controls()

        def submit():
            try:
                state = self.controller.apply(mode, percent)
            except Exception:
                state = FanState(available=self.last_state.available,
                                 error="apply", message="设置失败，请重试")
            GLib.idle_add(self.finish_apply, state, mode, percent)

        threading.Thread(target=submit, daemon=True, name="launcher-fan-apply").start()

    def finish_apply(self, state, mode, percent):
        if self.closed:
            return GLib.SOURCE_REMOVE
        self.busy = False
        self.apply_button.set_label("应用")
        self.render_state(state, result=True)
        if state.error:
            self.message.set_text(state.message or "设置失败，请重试")
        elif state.available and state.mode == mode and (
                mode == "auto" or state.requested_percent == percent):
            prefix = "演示" if getattr(self.controller, "is_preview", False) else "已应用"
            self.message.set_text(f"{prefix} · 自动" if mode == "auto" else f"{prefix} · 手动 {percent}%")
        else:
            self.message.set_text(state.message or "设置尚未生效，请重试")
        self.feedback = True
        self.update_controls()
        return GLib.SOURCE_REMOVE
