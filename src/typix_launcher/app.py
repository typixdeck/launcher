#!/usr/bin/env python3
"""TypixDeck full-screen launcher and low-overhead application supervisor."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from . import desktop
from .status_ui import StatusStrip
from .settings import AutostartController
from .supervisor import Supervisor

APP_ID = "ai.typixdeck.launcher"


def css_path() -> Path:
    installed = Path("/usr/share/typix-launcher/typix-launcher.css")
    source = Path(__file__).resolve().parent / "typix-launcher.css"
    return source if source.exists() else installed


class Launcher(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window: Gtk.ApplicationWindow | None = None
        self.grid: Gtk.FlowBox | None = None
        self.count_label: Gtk.Label | None = None
        self.buttons: list[Gtk.Button] = []
        self.power_buttons: list[Gtk.Button] = []
        self.display_is_off = False
        self.columns = 3
        self.monitors: list[Gio.FileMonitor] = []
        self.reload_source = 0
        self.autostart = AutostartController()
        self.status_strip = None

    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
            return
        self.load_css()
        window = Gtk.ApplicationWindow(application=self)
        self.window = window
        window.set_title("TypixDeck")
        window.set_decorated(False)
        window.set_default_size(800, 600)
        window.connect("key-press-event", self.on_key_press)
        window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.TOUCH_MASK)
        window.connect("button-press-event", self.on_pointer_event)
        window.connect("touch-event", self.on_pointer_event)
        window.connect("size-allocate", self.on_size_allocate)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_border_width(20)
        root.get_style_context().add_class("launcher-root")
        window.add(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="TypixDeck", xalign=0)
        title.get_style_context().add_class("launcher-title")
        subtitle = self.count_label = Gtk.Label(label="桌面快捷方式", xalign=0)
        subtitle.get_style_context().add_class("launcher-subtitle")
        title_box.pack_start(title, False, False, 0)
        title_box.pack_start(subtitle, False, False, 0)
        header.pack_start(title_box, True, True, 0)
        self.status_strip = StatusStrip()
        header.pack_start(self.status_strip, False, False, 0)
        window.connect("notify::is-active", lambda win, _prop: self.status_strip.set_active(win.is_active()))
        refresh = Gtk.Button(label="刷新")
        refresh.set_tooltip_text("重新扫描桌面应用（F5）")
        refresh.connect("clicked", lambda _button: self.reload())
        refresh.get_style_context().add_class("header-button")
        header.pack_start(refresh, False, False, 0)
        settings = Gtk.Button(label="设置")
        settings.set_tooltip_text("打开启动器设置（F9）")
        settings.get_style_context().add_class("header-button")
        settings.connect("clicked", lambda _button: self.show_settings())
        header.pack_start(settings, False, False, 0)
        root.pack_start(header, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_kinetic_scrolling(True)
        scroll.set_overlay_scrolling(True)
        self.grid = Gtk.FlowBox()
        self.grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.grid.set_homogeneous(True)
        self.grid.set_halign(Gtk.Align.FILL)
        self.grid.set_valign(Gtk.Align.START)
        self.grid.set_hexpand(True)
        self.grid.set_min_children_per_line(3)
        self.grid.set_max_children_per_line(3)
        self.grid.set_row_spacing(14)
        self.grid.set_column_spacing(14)
        scroll.add(self.grid)
        root.pack_start(scroll, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hint = Gtk.Label(label="方向键移动 · Enter 打开 · F5 刷新 · F9 设置 · F10 电源", xalign=0)
        hint.get_style_context().add_class("launcher-hint")
        footer.pack_start(hint, True, True, 0)

        power_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        screen_off = Gtk.Button(label="息屏")
        screen_off.set_tooltip_text("关闭显示；按任意键或触摸屏幕唤醒")
        screen_off.connect("clicked", self.screen_off)
        screen_off.get_style_context().add_class("power-button")
        restart = Gtk.Button(label="重启")
        restart.set_tooltip_text("重新启动 TypixDeck")
        restart.connect("clicked", self.confirm_power, "reboot")
        restart.get_style_context().add_class("power-button")
        shutdown = Gtk.Button(label="关机")
        shutdown.set_tooltip_text("安全关闭 TypixDeck")
        shutdown.connect("clicked", self.confirm_power, "poweroff")
        shutdown.get_style_context().add_class("power-button")
        shutdown.get_style_context().add_class("danger")
        self.power_buttons = [screen_off, restart, shutdown]
        for button in self.power_buttons:
            button.set_can_focus(True)
            power_box.pack_start(button, False, False, 0)
        footer.pack_end(power_box, False, False, 0)
        root.pack_start(footer, False, False, 0)

        self.reload()
        self.watch_desktop_dirs()
        window.show_all()
        window.fullscreen()
        GLib.idle_add(self.ensure_fullscreen)

    def do_shutdown(self) -> None:
        if self.status_strip is not None:
            self.status_strip.close()
        Gtk.Application.do_shutdown(self)

    def load_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_path(str(css_path()))
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def ensure_fullscreen(self) -> bool:
        if self.window is not None:
            self.window.fullscreen()
        return GLib.SOURCE_REMOVE

    def watch_desktop_dirs(self) -> None:
        for directory in desktop.default_desktop_dirs():
            try:
                monitor = Gio.File.new_for_path(str(directory)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
                monitor.connect("changed", self.on_directory_changed)
                self.monitors.append(monitor)
            except GLib.Error:
                continue

    def on_directory_changed(self, *_args) -> None:
        if self.reload_source:
            GLib.source_remove(self.reload_source)
        self.reload_source = GLib.timeout_add(250, self.finish_directory_reload)

    def finish_directory_reload(self) -> bool:
        self.reload_source = 0
        self.reload()
        return GLib.SOURCE_REMOVE

    def reload(self) -> None:
        if self.grid is None:
            return
        for child in self.grid.get_children():
            self.grid.remove(child)
        self.buttons.clear()
        apps = desktop.load_apps()
        for app in apps:
            button = self.make_tile(app)
            self.buttons.append(button)
            self.grid.add(button)
        if not apps:
            message = Gtk.Label(label="桌面还没有应用快捷方式\n添加 .desktop 快捷方式后会显示在这里")
            message.set_line_wrap(True)
            message.get_style_context().add_class("launcher-hint")
            self.grid.add(message)
        self.grid.show_all()
        if self.count_label is not None:
            self.count_label.set_text(f"{len(apps)} 个桌面应用")
        if self.buttons:
            GLib.idle_add(self.buttons[0].grab_focus)

    def make_tile(self, app: desktop.DesktopEntry) -> Gtk.Button:
        button = Gtk.Button()
        button.set_size_request(180, 150)
        button.set_hexpand(True)
        button.set_can_focus(True)
        button.get_style_context().add_class("app-tile")
        button.connect("clicked", self.launch, app.path)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        content.set_border_width(12)
        image = self.make_icon(app.icon)
        image.set_halign(Gtk.Align.CENTER)
        name = Gtk.Label(label=app.name)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(20)
        name.get_style_context().add_class("app-name")
        category = Gtk.Label(label=app.category)
        category.get_style_context().add_class("app-category")
        content.pack_start(image, False, False, 0)
        content.pack_start(name, False, False, 0)
        content.pack_start(category, False, False, 0)
        button.add(content)
        button.set_tooltip_text(app.comment)
        return button

    def make_icon(self, icon: str) -> Gtk.Image:
        icon_path = Path(icon)
        if icon_path.is_absolute() and icon_path.exists():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), 56, 56, True)
                return Gtk.Image.new_from_pixbuf(pixbuf)
            except GLib.Error:
                pass
        image = Gtk.Image.new_from_icon_name(icon or "application-x-executable", Gtk.IconSize.DIALOG)
        image.set_pixel_size(56)
        return image

    def launch(self, _button: Gtk.Button, path: Path) -> None:
        try:
            desktop.atomic_request(path)
        except OSError as exc:
            self.show_error("无法提交启动请求", str(exc))
            return
        self.quit()

    def show_error(self, title: str, detail: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        dialog.format_secondary_text(detail)
        dialog.run()
        dialog.destroy()

    def show_settings(self) -> None:
        if self.window is None:
            return
        dialog = Gtk.Dialog(title="启动器设置", transient_for=self.window, modal=True)
        dialog.set_default_size(560, 240)
        dialog.add_button("关闭", Gtk.ResponseType.CLOSE)
        content_area = dialog.get_content_area()
        content_area.set_spacing(14)
        if dialog.get_header_bar() is None:
            dialog.set_titlebar(Gtk.HeaderBar())
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_border_width(18)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        label = Gtk.Label(label="开机自动启动", xalign=0)
        label.set_hexpand(True)
        detail = Gtk.Label(label="登录图形会话后启动 TypixDeck 启动器", xalign=0)
        detail.get_style_context().add_class("launcher-hint")
        text_box.pack_start(label, False, False, 0)
        text_box.pack_start(detail, False, False, 0)
        switch = Gtk.Switch()
        switch.set_halign(Gtk.Align.END)
        switch.set_can_focus(True)
        state = self.autostart.state()
        if not state.available:
            switch.set_sensitive(False)
            detail.set_text("当前环境没有可用的 systemctl 用户会话")
        switch.set_active(state.enabled)
        switch.connect("state-set", self.on_autostart_changed)
        row.pack_start(text_box, True, True, 0)
        row.pack_start(switch, False, False, 0)
        content_area.pack_start(row, False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def on_autostart_changed(self, _switch: Gtk.Switch, value: bool) -> bool:
        try:
            state = self.autostart.set_enabled(value)
        except (OSError, RuntimeError) as exc:
            self.show_error("无法保存开机自动启动", str(exc))
            return True
        _switch.set_active(state.enabled)
        return True

    def confirm_power(self, _button: Gtk.Button, action: str) -> None:
        labels = {
            "reboot": ("确认重启？", "所有应用会被关闭，设备随后重新启动。", "重启"),
            "poweroff": ("确认关机？", "请等待屏幕熄灭后再断开电源。", "关机"),
        }
        title, detail, confirm_label = labels[action]
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=title,
        )
        dialog.set_decorated(False)
        dialog.format_secondary_text(detail)
        dialog.add_button("取消", Gtk.ResponseType.CANCEL)
        dialog.add_button(confirm_label, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        try:
            result = subprocess.run(
                ["systemctl", action],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            self.show_error(f"无法{confirm_label}", str(exc))
            return
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip()
            self.show_error(f"无法{confirm_label}", detail or "systemctl 拒绝了本次电源操作")
            return
        self.quit()

    def screen_off(self, _button: Gtk.Button) -> None:
        self.display_is_off = True
        GLib.timeout_add(120, self.finish_screen_off)

    def finish_screen_off(self) -> bool:
        if self.display_is_off:
            subprocess.run(["wlopm", "--off", "*"], check=False)
        return GLib.SOURCE_REMOVE

    def wake_display(self) -> None:
        if not self.display_is_off:
            return
        self.display_is_off = False
        subprocess.run(["wlopm", "--on", "*"], check=False)

    def on_pointer_event(self, _window: Gtk.Window, _event: Gdk.Event) -> bool:
        if not self.display_is_off:
            return False
        self.wake_display()
        return True

    def on_size_allocate(self, _window: Gtk.Window, allocation: Gdk.Rectangle) -> None:
        columns = 2 if allocation.width < 700 else 3
        if self.grid is not None and columns != self.columns:
            self.columns = columns
            self.grid.set_min_children_per_line(columns)
            self.grid.set_max_children_per_line(columns)

    def focused_index(self) -> int | None:
        for index, button in enumerate(self.buttons):
            if button.has_focus():
                return index
        return None

    def focus(self, index: int) -> None:
        if self.buttons:
            self.buttons[max(0, min(index, len(self.buttons) - 1))].grab_focus()

    def on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if self.display_is_off:
            self.wake_display()
            return True
        for power_index, power_button in enumerate(self.power_buttons):
            if not power_button.has_focus():
                continue
            if event.keyval == Gdk.KEY_Left:
                self.power_buttons[max(0, power_index - 1)].grab_focus()
            elif event.keyval == Gdk.KEY_Right:
                self.power_buttons[min(len(self.power_buttons) - 1, power_index + 1)].grab_focus()
            elif event.keyval == Gdk.KEY_Up and self.buttons:
                self.focus(max(0, len(self.buttons) - self.columns + power_index))
            elif event.keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space}:
                power_button.clicked()
            elif event.keyval == Gdk.KEY_F5:
                self.reload()
            elif event.keyval == Gdk.KEY_F9:
                self.show_settings()
            elif event.keyval in {Gdk.KEY_Escape, Gdk.KEY_F11}:
                self.ensure_fullscreen()
            else:
                return False
            return True
        if event.keyval == Gdk.KEY_F9:
            self.show_settings()
            return True
        if event.keyval == Gdk.KEY_F10 and self.power_buttons:
            self.power_buttons[0].grab_focus()
            return True
        if not self.buttons:
            if self.power_buttons and event.keyval in {Gdk.KEY_Down, Gdk.KEY_Up, Gdk.KEY_Left, Gdk.KEY_Right}:
                self.power_buttons[0].grab_focus()
                return True
            return event.keyval in {Gdk.KEY_Escape, Gdk.KEY_F11}
        index = self.focused_index()
        if index is None and event.keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space}:
            return False  # Let the focused header/status button activate normally.
        if index is None:
            index = 0
        if event.keyval == Gdk.KEY_Left:
            self.focus(index - 1)
        elif event.keyval == Gdk.KEY_Right:
            self.focus(index + 1)
        elif event.keyval == Gdk.KEY_Up:
            self.focus(index - self.columns)
        elif event.keyval == Gdk.KEY_Down:
            if index + self.columns >= len(self.buttons) and self.power_buttons:
                self.power_buttons[0].grab_focus()
            else:
                self.focus(index + self.columns)
        elif event.keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space}:
            self.buttons[index].clicked()
        elif event.keyval == Gdk.KEY_Home:
            self.focus(0)
        elif event.keyval == Gdk.KEY_End:
            self.focus(len(self.buttons) - 1)
        elif event.keyval == Gdk.KEY_F5:
            self.reload()
        elif event.keyval in {Gdk.KEY_Escape, Gdk.KEY_F11}:
            self.ensure_fullscreen()
        else:
            return False
        return True


def main() -> int:
    GLib.set_prgname(APP_ID)
    if "--supervisor" in sys.argv:
        return Supervisor().run_forever()
    app = Launcher()
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
