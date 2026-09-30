# 在 CM4 上修改 Launcher

CM4 工作目录：`/home/pi/Workspace/typixdeck/apps/launcher`。这是 [typixdeck/launcher](https://github.com/typixdeck/launcher) 的 Git checkout；系统安装的 Launcher 不会随源码编辑自动改变。

## 先体验

可以对 CM4 上的 ChatGPT 说：

> 请在 /home/pi/Workspace/typixdeck/apps/launcher 中工作。先读 AGENTS.md，检查 Git 状态和远端更新。把应用卡片的图标放大一点，保留三列布局和键盘操作，运行测试并打开源码预览。先不安装 deb。

```sh
cd /home/pi/Workspace/typixdeck/apps/launcher
python3 tools/preview.py
```

预览使用本次 checkout 的 Python 和 CSS，独立应用 ID，全屏显示；Esc 退出后回到原应用。用演示快捷方式和电量/Wi-Fi 数据，点击电源/息屏/应用按钮只显示说明，自动启动开关仅改变预览内存状态。修改后关闭并重新运行即可看到效果。不要通过 `python3 -m typix_launcher` 做隔离预览，它使用正式应用 ID 和设备接口。

主要文件：`src/typix_launcher/app.py`（布局/键盘）、`typix-launcher.css`（样式）、`status_ui.py`（图标）、`desktop.py`（快捷方式筛选）。

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
GDK_BACKEND=x11 python3 tools/preview.py --screenshot build/voice-preview.png
PYTHONPATH=src GDK_BACKEND=x11 python3 tools/check-status-ui.py /tmp/launcher-layout.png
```

## 两台电脑交接

GitHub 是共享代码源。Git 不会自动同步未提交的编辑；换机器前提交并 push，另一台先 pull。无需双向 rsync 或后台自动覆盖。

```sh
# 开工：先确认没有未提交修改
git status --short --branch
git switch main
git pull --ff-only
git switch -c cm4/card-layout
# 修改、验证、检查 diff 后，逐个暂存本次文件
git add src/typix_launcher/app.py src/typix_launcher/typix-launcher.css
git diff --cached
git commit -m "Adjust launcher card layout"
git push -u origin HEAD
```

另一台电脑执行 `git fetch origin`，首次用 `git switch --track origin/cm4/card-layout`，已有该分支则 `git switch cm4/card-layout && git pull --ff-only`。同一任务两端沿用同一分支；若分叉，保留两端提交并处理冲突，不强推。验收后通过 GitHub PR 或正常 merge 合并 main，再分别更新两端 main。仓库级 `pull.ff=only` 会拒绝自动生成意外合并。

拉取公开仓库不需要账号；push 必须先完成 CM4 自己的 GitHub 授权。仓库禁用了 deploy key，因此未采用设备专用 deploy key。不要复制 Mac 的私钥或把 token 放进 remote URL。授权未完成时可以继续本地修改、测试和 commit，暂不 push。

源码通过后，正式更新需要单独构建、核验与安装 deb；push 本身不会升级设备上的应用。
