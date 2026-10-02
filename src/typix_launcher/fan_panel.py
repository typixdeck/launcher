"""Settings-only fan controls; worker threads never touch GTK widgets."""
from __future__ import annotations

import threading

from gi.repository import GLib, Gtk

from .fan import DEFAULT_CURVE, FanError, FanState, validate_curve
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
        self.dirty = False
        self.loading = False
        self.validation_error = ''
        self.set_border_width(18)
        self.get_style_context().add_class('fan-panel')
        self.connect("destroy", lambda _widget: self.close())

        header = Gtk.Box(spacing=12)
        title = Gtk.Label(label="风扇", xalign=0)
        title.set_hexpand(True)
        header.pack_start(title, True, True, 0)
        self.mode = Gtk.ComboBoxText()
        self.mode.set_name("fan-mode")
        for value, label in (("auto", "自动 · 温控曲线"), ("manual", "手动 · 固定转速")):
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
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 30, 100, 1)
        self.scale.set_name("fan-percent")
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.set_can_focus(True)
        self.scale.set_value(50)
        self.scale.connect("value-changed", self.on_draft_changed)
        self.percent_input = Gtk.SpinButton.new_with_range(30, 100, 1)
        self.percent_input.set_numeric(True)
        self.percent_input.set_value(50)
        self.percent_input.connect('value-changed', self.on_percent_input)
        self.percent = Gtk.Label(label="%")
        manual_row.pack_start(self.scale, True, True, 0)
        manual_row.pack_end(self.percent, False, False, 0)
        manual_row.pack_end(self.percent_input, False, False, 0)
        # Show the children before suppressing recursive show_all on the row.
        # Otherwise the row exists but its slider and percentage stay hidden.
        manual_row.show_all()
        manual_row.set_no_show_all(True)
        manual_row.hide()
        self.pack_start(manual_row, False, False, 0)

        self.curve_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.curve_area = Gtk.DrawingArea()
        self.curve_area.set_size_request(-1, 110)
        self.curve_area.connect('draw', self.draw_curve)
        self.curve_box.pack_start(self.curve_area, False, False, 0)
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        self.curve_inputs = []
        grid.attach(Gtk.Label(label='温度 °C', xalign=0), 0, 0, 1, 1)
        grid.attach(Gtk.Label(label='输出 %', xalign=0), 0, 1, 1, 1)
        for index, (temperature, duty) in enumerate(DEFAULT_CURVE):
            temp = Gtk.SpinButton.new_with_range(30, 80, 1)
            output = Gtk.SpinButton.new_with_range(100 if index == 3 else 30, 100, 1)
            temp.set_value(temperature)
            output.set_value(duty)
            for widget in (temp, output):
                widget.set_numeric(True)
                widget.set_hexpand(True)
                widget.connect('value-changed', self.on_draft_changed)
            grid.attach(temp, index + 1, 0, 1, 1)
            grid.attach(output, index + 1, 1, 1, 1)
            self.curve_inputs.append((temp, output))
        self.curve_box.pack_start(grid, False, False, 0)
        self.reset_curve = Gtk.Button(label='恢复默认曲线')
        self.reset_curve.set_halign(Gtk.Align.END)
        self.reset_curve.connect('clicked', self.on_reset_curve)
        self.curve_box.pack_start(self.reset_curve, False, False, 0)
        self.curve_box.show_all()
        self.curve_box.set_no_show_all(True)
        self.pack_start(self.curve_box, False, False, 0)

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
        self.percent_input.set_sensitive(enabled)
        self.curve_box.set_sensitive(enabled)
        self.apply_button.set_sensitive(enabled and not self.validation_error)
        manual = self.mode.get_active_id() == "manual"
        self.manual_row.set_visible(manual)
        self.curve_box.set_visible(not manual)
        self.hint.set_text("拖动滑块或输入 30–100% · 80°C 强制全速" if manual else
                           "编辑四个温度点，连续调速 · 80°C 强制全速")

    def curve_values(self):
        return tuple((temp.get_value_as_int(), duty.get_value_as_int()) for temp, duty in self.curve_inputs)

    def on_percent_input(self, widget):
        self.scale.set_value(widget.get_value())

    def on_reset_curve(self, _button):
        self.loading = True
        for (temp, duty), (temperature, percent) in zip(self.curve_inputs, DEFAULT_CURVE):
            temp.set_value(temperature)
            duty.set_value(percent)
        self.loading = False
        self.on_draft_changed(self.reset_curve)

    def draw_curve(self, widget, context):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        left, right, top, bottom = 36, max(50, width - 18), 14, height - 24
        x = lambda temperature: left + (right - left) * (temperature - 30) / 50
        y = lambda percent: bottom - (bottom - top) * (percent - 30) / 70
        context.set_source_rgb(.07, .14, .19)
        context.rectangle(0, 0, width, height)
        context.fill()
        context.set_font_size(11)
        for percent in (30, 65, 100):
            context.set_source_rgb(.18, .29, .36)
            context.move_to(left, y(percent)); context.line_to(right, y(percent)); context.stroke()
            context.set_source_rgb(.7, .8, .86)
            context.move_to(4, y(percent) + 4); context.show_text(str(percent))
        for temperature in (30, 40, 50, 60, 70, 80):
            context.set_source_rgb(.7, .8, .86)
            context.move_to(x(temperature) - 8, height - 6); context.show_text(str(temperature))
        points = self.curve_values()
        context.set_source_rgb(.26, .84, 1)
        context.set_line_width(2)
        context.move_to(x(30), y(points[0][1]))
        for temperature, percent in points:
            context.line_to(x(temperature), y(percent))
        context.line_to(x(80), y(100)); context.stroke()
        for temperature, percent in points:
            context.arc(x(temperature), y(percent), 4, 0, 6.283185)
            context.fill()
        return False

    def on_draft_changed(self, _widget):
        if self.loading:
            return
        self.percent_input.set_value(self.scale.get_value())
        self.curve_area.queue_draw()
        try:
            validate_curve(self.curve_values())
            self.validation_error = ''
        except FanError as error:
            self.validation_error = str(error)
        if self.initialized and not self.busy:
            self.dirty = True
            self.feedback = False
            self.message.set_text(self.validation_error or "点击应用保存")
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
            self.loading = True
            self.mode.set_active_id(state.mode if state.mode in ("auto", "manual") else "auto")
            self.scale.set_value(max(30, min(100, state.requested_percent)))
            self.percent_input.set_value(self.scale.get_value())
            for (temp, duty), (temperature, percent) in zip(self.curve_inputs, state.curve):
                temp.set_value(temperature)
                duty.set_value(percent)
            self.loading = False
            self.initialized = True
        self.readings.set_text(readings_label(state))
        if result or (not self.feedback and not self.dirty) or state.error or not state.available or state.mode == "off":
            if state.error or not state.available or state.mode == "off":
                self.feedback = False
            self.message.set_text(state.message or (
                "需启用 EMC2301 驱动" if not state.available else
                "尚未启用风扇控制" if state.mode == "off" else ""))
        self.update_controls()

    def on_apply(self, _button):
        if self.closed or self.busy or not self.last_state.available or self.validation_error:
            return
        mode = self.mode.get_active_id()
        percent = int(round(self.scale.get_value()))
        curve = validate_curve(self.curve_values())
        self.busy = True
        self.generation += 1
        self.message.set_text("正在应用，请完成系统授权…")
        self.apply_button.set_label("应用中…")
        self.update_controls()

        def submit():
            try:
                state = self.controller.apply(mode, percent, curve=curve)
            except Exception:
                state = FanState(available=self.last_state.available,
                                 error="apply", message="设置失败，请重试")
            GLib.idle_add(self.finish_apply, state, mode, percent, curve)

        threading.Thread(target=submit, daemon=True, name="launcher-fan-apply").start()

    def finish_apply(self, state, mode, percent, curve):
        if self.closed:
            return GLib.SOURCE_REMOVE
        self.busy = False
        self.apply_button.set_label("应用")
        self.render_state(state, result=True)
        if state.error:
            self.message.set_text(state.message or "设置失败，请重试")
        elif state.available and state.mode == mode and state.curve == curve and (
                mode == "auto" or state.requested_percent == percent):
            prefix = "演示" if getattr(self.controller, "is_preview", False) else "已应用"
            self.message.set_text(f"{prefix} · 温控曲线" if mode == "auto" else f"{prefix} · 手动 {percent}%")
            self.dirty = False
        else:
            self.message.set_text(state.message or "设置尚未生效，请重试")
        self.feedback = True
        self.update_controls()
        return GLib.SOURCE_REMOVE
