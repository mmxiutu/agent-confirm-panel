# agent-confirm-panel

在旁边的手机上批准或拒绝 Cursor Agent 的终端命令和 MCP 调用。手机不在身边时，这些操作仍由 Cursor 自己确认。

仓库里有两个程序：

- `windows/`：跑在电脑上的 Python 服务，以及 Cursor 钩子脚本。
- `app/`：uni-app x 客户端。同一套工程可以编译到安卓、iOS 和鸿蒙。当前真机目标仍是红米 Note 11T Pro，iOS 和鸿蒙还没有跑过。

Hermes 不在本仓库范围内。语音按钮只占位，还不能识别指令。

## 它怎么工作

Cursor 在执行终端命令或 MCP 工具之前，会运行用户目录里的钩子 `windows/cursor_hook.py`。钩子先询问本机 `8765` 端口：

| 状态 | 结果 |
| --- | --- |
| 没有启动 `windows/server.py`，或手机面板还没连上 | 钩子返回 `ask`。Cursor 按自己的规则执行，或弹出 Cursor 自己的确认框。 |
| 服务已启动，并且手机面板已连接 | 命令显示在手机上。同意后继续，拒绝、终止或超时后取消。 |

读文件、搜索、改文件不经过这个钩子。SourceTree 和系统自带的命令行也不经过它，所以用它们提交 Git 不受影响。

同一时间只处理一个待审批命令。审批大约 170 秒没有操作，按拒绝处理。只启动 `windows/server.py` 这件事本身不需要再审批，否则服务停着时无法把它重新开起来。

Auto-review 下，Cursor 认为安全的命令即使收到 `ask` 也可能直接执行。这和没装钩子时一样。

## 电脑端

需要 Python 3.10 或更高版本。

```bat
py -3.13 -m pip install -r windows\requirements.txt
py -3.13 windows\server.py
```

服务监听 `0.0.0.0:8765`，启动时会打印局域网 IP。手机设置页要填这个 IP，不要填 `127.0.0.1`。手机和电脑必须在同一个局域网。Windows 防火墙要对专用网络放行 TCP 8765。

没有手机时，可以用本机请求模拟一次批准。`request_id` 看服务窗口里的日志：

```bat
curl.exe -X POST -H "Content-Type: application/json" -d "{\"request_id\":\"日志里的id\",\"action\":\"approve\"}" http://127.0.0.1:8765/action
```

`action` 还可以是 `reject` 或 `stop_task`。这个地址只接受本机调用。

## 手机端

用 HBuilderX 打开 `app` 目录。`manifest.json` 里有 `uni-app-x` 节点，项目应识别为 uni-app x（圆形图标）。若仍被识别成旧版 uni-app，在项目上右键选择重新识别项目类型。

红米上运行时，选择 uni-app x 的 Android 标准基座，不要用旧的 uni-app 基座。在 App 的设置页填写电脑局域网 IP 和端口 `8765`。连接使用局域网明文 `ws://`，`app/AndroidManifest.xml` 已允许安卓明文流量。iOS 和鸿蒙要另选对应基座；常亮目前走的是安卓窗口标志，换平台时需要再核对。

手机上还需要手动打开这些权限，程序绕不过系统限制：允许网络与震动、关闭该 App 的电池优化、关闭自动锁屏。连接成功后，App 会保持屏幕常亮；断开后释放。语音按钮仍未实现。

## 换项目，或换一台电脑

钩子不在项目目录里，而在 **Windows 用户目录**：

`C:\Users\<用户名>\.cursor\hooks.json`

所以：

- **同一台电脑上的新项目**：不用再改。只要这个用户的 `hooks.json` 还指着本仓库的 `windows\cursor_hook.py`，所有项目里的 Agent 终端命令和 MCP 都会走同一套规则。
- **一台新电脑**：先克隆本仓库并装好 Python 依赖，再让 Agent 改那台电脑自己的用户目录 `hooks.json`。不要把这台机器上的绝对路径抄到另一台电脑。

`hooks.json` 里目前登记的是 `beforeShellExecution` 和 `beforeMCPExecution`，`timeout` 为 180 秒，`failClosed` 为 true。合并时要保留文件里已经存在的其他钩子。

### 提示词：同一台电脑，新项目

```text
这个项目不需要再配置 agent-confirm-panel。
审批钩子已经写在本机用户目录的 .cursor/hooks.json 里，对所有项目生效。
请不要修改 ~/.cursor/hooks.json，也不要在这个项目里再加一份钩子。
```

### 提示词：新电脑

把仓库先克隆到新电脑，然后在新电脑的 Cursor 里把下面整段发给 Agent。把路径换成那台电脑上的实际路径。

```text
请阅读本仓库的 README.md 和 windows/cursor_hook.py、windows/server.py。
在这台电脑上启用 agent-confirm-panel，不要改 Cursor 程序本身。

目标行为：
1. 只拦截 Cursor Agent 的终端命令（beforeShellExecution）和 MCP 调用（beforeMCPExecution）。
   不要拦截读文件、搜索和改文件。
2. 钩子写在用户目录的 hooks.json，不要写进某一个项目的 .cursor/hooks.json。
   合并时保留已有钩子。使用 python.exe 的绝对路径和本仓库 windows/cursor_hook.py 的绝对路径。
   timeout 设为 180，failClosed 设为 true。
3. windows/server.py 监听 0.0.0.0:8765。
   手机面板已通过 WebSocket 连上时，命令推到手机，同意返回 allow，拒绝、终止或超时返回 deny。
   服务没启动，或没有任何手机连接时，钩子返回 ask，让 Cursor 使用自己的确认逻辑，不要直接 deny。
4. 仅启动 windows/server.py 的命令直接 allow，避免服务停着时无法重新打开。
5. 不要引入 FastAPI。不要做网页版。客户端在 app 目录，保持 uni-app x，不要改回旧版 uni-app，也不要把目录改回 android。

改完后告诉我：hooks.json 的位置、启动服务的命令、手机上要填的局域网 IP。
```
