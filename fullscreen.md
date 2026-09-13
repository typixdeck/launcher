# 应用启动全屏

Launcher 的 supervisor 在启动应用前创建 `FullscreenSession`，先取得现有窗口快照，再启动桌面快捷方式的命令。CM4 的官方 Raspberry Pi OS 上已探测到 Labwc 0.9.8 提供 `zwlr_foreign_toplevel_manager_v1` version 3；这个接口覆盖原生 Wayland 与 XWayland 窗口。实现只依赖 Python 标准库，并且始终运行在当前用户权限下。

```python
with FullscreenSession(path) as session:
    child = subprocess.Popen(command, cwd=working_dir)
    return session.wait(child)
```

应用 ID 只进行完整匹配，默认来自 `.desktop` 文件名、`StartupWMClass` 和非通用解释器的 `Exec` 可执行文件名。包装脚本与实际窗口 ID 不一致时，在用户桌面快捷方式中填写经过真机验证的值，例如：

```ini
X-TypixDeck-FullscreenAppId=org.example.Player;ExamplePlayer;
```

这个字段优先于推断值，支持分号分隔的多个 ID。它不接受通配符，也不按窗口标题匹配。桌面 `Type=Link` 会沿用目标应用入口的 metadata。

仅在最长 10 秒启动区间内，对匹配的主窗口发送幂等的 `set_fullscreen`。尚未收到全屏状态确认时，最多每 0.5 秒重试一次，覆盖正常唤醒期间 compositor 暂时忽略请求的情况；收到确认后停止重试。现有非活动窗口、其他应用窗口以及有父窗口的对话框不会被修改；单实例应用激活已经打开的匹配窗口时也可以处理。已经全屏的窗口保留现状，用户之后自行退出全屏也不会被循环强制恢复。代码不控制 DPMS，没有发送 `ToggleFullscreen` 或模拟键盘输入。

启动器命令派生应用后立即退出时，supervisor 会继续等待已匹配窗口关闭，避免 Launcher 抢回前台。在启动区间内，最后一个匹配窗口关闭后保留 0.75 秒宽限，允许启动画面切换到主窗口；宽限不会延长 10 秒启动区间。失败的启动命令及时返回；成功但没有匹配窗口的命令最多等待启动区间。协议断开、协议版本不足、未使用 Wayland 等场景会保留应用自己的全屏行为，并回退到等待子进程退出。自动全屏不依赖 CM 型号字符串；在其他 compositor 上应重新验证所探测的协议能力。

只读验证命令：

```sh
PYTHONPATH=apps/launcher/src python3 -m typix_launcher.fullscreen --snapshot
PYTHONPATH=apps/launcher/src python3 -m unittest discover -s apps/launcher/tests -p test_fullscreen.py -v
```

`--snapshot` 只向标准输出返回当前窗口的 app_id、标题、父窗口与全屏状态，不持久化或上传窗口/应用清单。真机验收记录只保留被测窗口结果。

真机验收应包含：原生 Wayland 主窗口、`GDK_BACKEND=x11` 的 XWayland 主窗口、已经全屏的窗口、带父窗口的对话框、立即退出的失败命令，以及派生应用后提前退出的启动脚本。前四项通过同一只读快照确认对应窗口状态；后两项确认 Launcher 恢复时机。不要把 compositor 的“请求已发送”当作“全屏已确认”。

验收前用 `wlopm` 只读确认输出处于 `on`。Labwc 在输出被 DPMS 休眠时可能拒绝全屏请求；`wlr-randr` 的 `Enabled: yes` 不能代替 DPMS 状态检查。测试用 GTK3 程序应在创建 Application 前调用 `GLib.set_prgname` 设置预期 Wayland app_id，`Gdk.set_program_class` 用于 X11 WMClass；仅设置 Gtk.Application 的 application_id 不足以保证窗口 ID 一致。

协议来源：[wlr foreign toplevel management v3](https://gitlab.freedesktop.org/wlroots/wlr-protocols/-/blob/master/unstable/wlr-foreign-toplevel-management-unstable-v1.xml)。
