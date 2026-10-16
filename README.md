# agent-confirm-panel

在旁边的手机上处理 Cursor Agent 的人工确认。手机不在身边时，这些操作仍由 Cursor 自己完成。

它做三件事：

1. **批准或拒绝**终端命令和 MCP 调用。
2. **回答选择题**。指令不清楚，或方案有多处可调时，手机上点选项，也可以自己写一句。
3. **标明来源**。每条待办都显示它来自哪个项目、哪一个 Agent 聊天框。

仓库里有两个程序：

- `windows/`：电脑上的 Python 服务、Cursor 钩子、提问用的 MCP。
- `app/`：uni-app x 客户端。同一套工程可以编译到安卓、iOS 和鸿蒙。当前真机是红米 Note 11T Pro，iOS 和鸿蒙还没有跑过。

Hermes 不在本仓库范围内。语音按钮只占位，还不能识别指令。

## 日常怎么用

1. 在电脑上启动服务，并让这个窗口一直开着。关掉窗口，手机就会断开。

```bat
py -3.13 -m pip install -r windows\requirements.txt
py -3.13 windows\server.py
```

也可以双击 `windows\start.bat`。它使用本机的 Python 3.13。

2. 看服务窗口打印的局域网 IP。手机和电脑必须在同一个局域网。手机设置页填其中一个 IP 和端口 `8765`，不要填 `127.0.0.1`。电脑有多块网卡时，窗口会打印多个地址；手机显示未连接就换另一个。Windows 防火墙要对专用网络放行 TCP 8765。

3. 手机设置键变成 **[已连接]** 后就可以用。服务窗口同时会出现 `client connected`。连接成功后 App 保持屏幕常亮，断开后释放。

启动 `windows\server.py` 本身不会再要求手机批准，否则服务停着时无法把它重新开起来。

改完代码后的对应关系：

| 改了什么 | 要做什么 |
| --- | --- |
| `windows/server.py` | 关掉服务窗口，重新运行。正在跑的进程不会自己换成新代码。 |
| `windows/cursor_hook.py` | 不用重启服务。下一次钩子会启动新的 Python 进程。 |
| `windows/panel_mcp.py` | 在 Cursor 里重新加载 MCP。 |
| `app/` 里的界面 | 用 HBuilderX 重新运行到手机。 |

## 手机上看到什么

待处理时，状态标题下面有一行来源，例如 `agent-confirm-panel · 补上选项和来源`。

- 标题优先取这个聊天记录里的第一条用户消息，并截短。
- 还没有记录时，显示会话 id 的前 8 位。
- 子任务显示为 `子任务 …`，后台 Agent 显示为 `后台 …`。
- 对不上聊天时，标题是 `未识别的聊天`，避免把别的聊天的名字套上去。
- 点「详情」可以看到完整会话 id、命令、路径和风险。

授权（橙色）底栏是：同意、拒绝、详情、终止、设置。

选择题（深琥珀色）底栏是：提交、拒绝、详情、终止、设置。每道题是大块选项；标了「可多选」的可以点多个。每题下面都有「自定义」。每道题至少要点一个选项，或写一句自定义，才能提交。自定义可以单独提交，也可以和已选选项一起提交。

同一时间只有一个待办。另一个聊天在前一个还没处理完时会遇到忙碌，不会叠在同一屏上。大约 170 秒没有操作，按拒绝处理。

## 两类请求分别怎么走

### 终端命令和 MCP 调用

Cursor 在执行前运行用户目录里的钩子 `windows/cursor_hook.py`。钩子先看本机 `8765`：

| 状态 | 结果 |
| --- | --- |
| 没有启动服务，或手机还没连上 | 钩子返回 `ask`。Cursor 按自己的规则执行，或弹出 Cursor 自己的确认框。 |
| 服务已启动，并且手机已连接 | 命令显示在手机上。同意后继续，拒绝、终止或超时后取消。 |

读文件、搜索、改文件不经过这个钩子。SourceTree 和系统自带的命令行也不经过它，所以用它们提交 Git 不受影响。

Auto-review 下，Cursor 认为安全的命令即使收到 `ask` 也可能直接执行。这和没装钩子时一样。

### 选项和自定义回答

Cursor 自带的 AskQuestion **不会触发任何钩子**，手机收不到桌面上那个选项框。这是 Cursor 目前的限制，没有拦截办法。

需要用户做选择时，Agent 调用 MCP 工具 `agent-confirm-panel` / `ask`，不要调用 AskQuestion。这个工具的名字必须和 `mcp.json` 里的服务名一致，钩子才会直接放行。否则手机会先被要求批准这次提问，再回答一次。

`ask` 会等到手机提交、拒绝、终止或超时。返回给 Agent 的结果：

| 结果 | Agent 应该怎么做 |
| --- | --- |
| `action` 为 `choose` | 按 `answers` 里的 `option_ids`、`labels` 和 `custom` 继续。`custom` 不是空字符串时，以用户写下的文字为准。 |
| `reason` 为 `panel_offline` | 面板没连上。改用 AskQuestion，在桌面提问。 |
| `reason` 为 `panel_busy` | 面板正处理另一个聊天。稍后重试一次 `ask`。仍然忙碌就改用 AskQuestion。 |
| `action` 为 `reject` | 用户拒绝回答。不要重复同一问题。 |
| `action` 为 `stop_task` | 用户要求终止。停止当前任务。 |
| `action` 为 `timeout` | 用户没有及时回答。不要继续执行有风险的操作。 |

一次最多 6 道题，每题最多 8 个选项。每题都要有 `id`、`prompt` 和 `options`（每项有 `id`、`label`）。可多选时设置 `allow_multiple: true`。手机上的自定义输入不用在参数里再声明。

本机用户规则写在 `~\.cursor\rules\agent-confirm-panel.mdc`，对所有项目生效。它要求 Agent 按上面的约定调用 `ask`。

## 给 Agent 的约定

读到这份说明时，按下面做：

- 终端命令和其它 MCP 调用继续走手机批准。不要为了绕过批准而改写命令。
- 需要用户在多个方案里选，或允许用户用自己的话补充时，调用 `agent-confirm-panel` 的 `ask`。
- 不要调用 AskQuestion，除非 `ask` 返回 `panel_offline`，或重试后仍是 `panel_busy`。
- 不要把 `ask` 当成又一次需要批准的 MCP。钩子已经对这个工具返回 allow。
- 同一台电脑上的新项目不要再改 `~\.cursor\hooks.json`，也不要在项目里再放一份钩子。
- 不要引入 FastAPI，不要做网页版。客户端保持 uni-app x。

## 电脑端调试

没有手机时，可以用本机请求模拟一次批准。`request_id` 看服务窗口里的日志。这个地址只接受本机调用。

```bat
curl.exe -X POST -H "Content-Type: application/json" -d "{\"request_id\":\"日志里的id\",\"action\":\"approve\"}" http://127.0.0.1:8765/action
```

`action` 还可以是 `reject`、`stop_task` 或 `choose`。`choose` 要带上 `answers`。

模拟一道选择题。这条命令会一直等到手机提交、拒绝或超时：

```bat
curl.exe -X POST -H "Content-Type: application/json" -d "{\"status\":\"waiting_choice\",\"summary\":\"用哪种布局\",\"extra\":{\"tool\":\"choice\",\"source\":{\"conversation_id\":\"demo-chat\",\"project\":\"agent-confirm-panel\"},\"questions\":[{\"id\":\"layout\",\"prompt\":\"列表用哪种布局？\",\"options\":[{\"id\":\"a\",\"label\":\"保持网格\"},{\"id\":\"b\",\"label\":\"改成单列\"}]}]}}" http://127.0.0.1:8765/push
```

直接往 `/push` 写入来源时，服务不会替你读聊天记录。真实使用时，钩子会带上 `conversation_id` 和记录路径，服务读取第一条用户消息作为标题，并按会话 id 缓存。

## 手机端

用 HBuilderX 打开 `app` 目录。`manifest.json` 里有 `uni-app-x` 节点，项目应识别为 uni-app x（圆形图标）。若仍被识别成旧版 uni-app，在项目上右键选择重新识别项目类型。

红米上运行时，选择 uni-app x 的 Android 标准基座，不要用旧的 uni-app 基座。连接使用局域网明文 `ws://`，`app/AndroidManifest.xml` 已允许安卓明文流量。iOS 和鸿蒙要另选对应基座；常亮目前走的是安卓窗口标志，换平台时需要再核对。

手机上还需要手动打开这些权限，程序绕不过系统限制：允许网络与震动、关闭该 App 的电池优化、关闭自动锁屏。

## 换项目，或换一台电脑

钩子、MCP 和用户规则都在 **Windows 用户目录**，不在某一个项目里：

- `C:\Users\<用户名>\.cursor\hooks.json`
- `C:\Users\<用户名>\.cursor\mcp.json`
- `C:\Users\<用户名>\.cursor\rules\agent-confirm-panel.mdc`

所以：

- **同一台电脑上的新项目**：不用再改。只要上述文件还指着本仓库，所有项目里的 Agent 都走同一套规则。
- **一台新电脑**：先克隆本仓库并装好 Python 依赖，再让 Agent 改那台电脑自己的用户目录。不要把这台机器上的绝对路径抄到另一台电脑。

`hooks.json` 里这些事件都指向同一个 `windows\cursor_hook.py`：

- `beforeShellExecution`、`beforeMCPExecution`：`timeout` 180，`failClosed` true
- `afterShellExecution`、`afterMCPExecution`：`timeout` 10，`failClosed` false
- `sessionStart`、`subagentStart`：`timeout` 5，`failClosed` false。用来记住聊天和子任务的标题

合并时要保留文件里已经存在的其他钩子。

`mcp.json` 里的 stdio 服务名必须是 `agent-confirm-panel`。命令用这台电脑的 `python.exe`，参数是 `-u` 和本仓库 `windows\panel_mcp.py` 的绝对路径。

### 提示词：同一台电脑，新项目

```text
这个项目不需要再配置 agent-confirm-panel。
审批钩子、提问 MCP 和用户规则已经写在本机用户目录，对所有项目生效。
请不要修改 ~/.cursor/hooks.json、~/.cursor/mcp.json 或 ~/.cursor/rules/agent-confirm-panel.mdc，
也不要在这个项目里再加一份钩子。
需要用户选择时，调用 MCP 工具 agent-confirm-panel 的 ask。用法见本仓库 README.md。
```

### 提示词：新电脑

把仓库先克隆到新电脑，然后在新电脑的 Cursor 里把下面整段发给 Agent。把路径换成那台电脑上的实际路径。

```text
请阅读本仓库的 README.md 和 windows/cursor_hook.py、windows/server.py、windows/panel_mcp.py。
在这台电脑上启用 agent-confirm-panel，不要改 Cursor 程序本身。

目标行为：
1. 只拦截 Cursor Agent 的终端命令（beforeShellExecution）和 MCP 调用（beforeMCPExecution）。
   不要拦截读文件、搜索和改文件。
2. 钩子写在用户目录的 hooks.json，不要写进某一个项目的 .cursor/hooks.json。
   合并时保留已有钩子。使用 python.exe 的绝对路径和本仓库 windows/cursor_hook.py 的绝对路径。
   beforeShellExecution 和 beforeMCPExecution 的 timeout 设为 180，failClosed 设为 true。
   同时登记 afterShellExecution、afterMCPExecution、sessionStart、subagentStart，后四个 failClosed 为 false。
3. windows/server.py 监听 0.0.0.0:8765。
   手机面板已通过 WebSocket 连上时，命令推到手机，同意返回 allow，拒绝、终止或超时返回 deny。
   服务没启动，或没有任何手机连接时，钩子返回 ask，让 Cursor 使用自己的确认逻辑，不要直接 deny。
   每次推送带上 conversation_id、项目名，并由服务从聊天记录取标题。
4. 在用户目录 mcp.json 注册 stdio 服务 agent-confirm-panel，指向 windows/panel_mcp.py。
   工具 ask 把选项和自定义回答发到手机。这个工具直接 allow，不要再让手机批准一次。
   写一条用户规则：需要选择或自定义说明时调用 ask，不要调用 AskQuestion；面板离线或忙碌时再退回 AskQuestion。
5. 仅启动 windows/server.py 的命令直接 allow，避免服务停着时无法重新打开。
6. 不要引入 FastAPI。不要做网页版。客户端在 app 目录，保持 uni-app x，不要改回旧版 uni-app，也不要把目录改回 android。

改完后告诉我：hooks.json 的位置、启动服务的命令、手机上要填的局域网 IP。
```
