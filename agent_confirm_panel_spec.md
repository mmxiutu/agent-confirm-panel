> 给 Cursor / VibeCoding 读取的需求规格文档
> 重要前置共识：**放弃网页/PWA方案，直接做安卓原生App**；网页形态在红米Note11T Pro上受浏览器、后台、语音、WakeLock限制太多，不适合长期专用面板场景。
> 目标硬件：红米 Note11T Pro（安卓，长期插电、支架固定作为专用Agent审批面板）
> 电脑侧：独立后台适配服务程序，用于对接 Cursor Agent、Hermes Agent Desktop([https://hermesagent.org.cn/](https://hermesagent.org.cn/))
> 通信：局域网 WebSocket（电脑 = Server；安卓App = Client）

## 项目名称
`agent-confirm-panel`

## 项目目标
实现一套【电脑端适配服务 + 安卓原生面板App】的Agent人机审批系统：
1. 电脑上的 Agent（Cursor Agent / Hermes Agent Desktop）执行任务遇到审批断点时，把状态、待审批信息发送给电脑本地适配服务；
2. 电脑适配服务通过局域网WebSocket推送给红米手机上的原生安卓App；
3. 安卓App全屏展示Agent状态、风险摘要、详情，提供触摸按钮、震动反馈、音效、**离线/在线语音指令识别**；
4. 用户在App操作（同意/拒绝/查看详情/终止）后，指令回传给电脑服务；电脑服务再转发审批结果给对应的Agent，继续或终止任务。

> 迭代原则：优先打通「电脑服务 ↔ 安卓App通信 + 状态流转 + 基础审批」，再叠加语音、动画等体验功能。

## 运行环境
### 电脑端（适配服务）
- Python 3.10+
- 依赖：`websockets`、`requests`
- 网络：和手机在同一个局域网；防火墙放行端口（默认 8765）
- 对接目标：
  - Cursor Agent（自定义回调/本地HTTP接口）
  - Hermes Agent Desktop 社区版 [https://hermesagent.org.cn/](https://hermesagent.org.cn/)

### 安卓端（原生App）
- 开发框架：**UniApp（uVue3）**，打包为安卓APK，直接安装到红米Note11T Pro
> 选择理由：上手快、可以编译成原生安卓包、权限管理可控，不需要深度Kotlin/Java；比Flutter对你这种快速vibe开发更友好；支持安卓常亮、震动、音频播放、麦克风语音、前台保活能力。
- 系统：HyperOS / MIUI（安卓12+）
- 手机手动前置配置（代码无法绕过系统策略，文档里告知用户）
  1. 长期插充电器供电
  2. 给这个App：关闭电池优化、关闭省电策略、允许后台活动、**开启前台权限**
  3. 系统自动锁屏关闭；App内额外代码层持有WakeLock锁兜底屏幕常亮
  4. 授予权限：麦克风、震动、前台服务

## 整体架构
```
Cursor Agent / Hermes Agent Desktop
        ↕（本地HTTP回调/接口）
电脑端适配服务（Python WebSocket Server）
        ↕（局域网WebSocket JSON）
UniApp安卓客户端（红米Note11T Pro，专用面板）
```
模块职责拆分：
1. **电脑适配服务 server.py**
   - 启动WebSocket服务，等待安卓App连接，维护在线客户端
   - 暴露本地HTTP接口，供Cursor / Hermes Agent推送状态、审批内容
   - 接收安卓App回传的用户操作指令
   - 根据配置，把审批结果转发给Agent的回调接口
   - 控制台输出完整日志：状态推送、用户操作、时间戳
2. **UniApp安卓App**
   - 启动后自动连接配置好的电脑局域网WS地址，支持手动重连
   - 连接成功：申请WakeLock保持屏幕常亮；断开连接释放常亮锁
   - 接收服务下发状态JSON，驱动UI、动画、提醒音、震动
   - 触摸按钮交互：播放音效、震动、发送action回传服务
   - 语音识别模块：监听麦克风，识别关键词，自动映射为审批动作
   - 全屏UI，深色D200H风格网格面板

## 数据协议（固定JSON，两边严格对齐）
### 1. Server → App（状态推送包）
```json
{
  "type": "state_update",
  "payload": {
    "status": "idle",
    "summary": "等待Agent任务",
    "detail": "",
    "extra": {
      "command": "",
      "file_path": "",
      "risk_level": "normal"
    }
  }
}
```
status 枚举（状态机全集）
- `idle`：空闲就绪 ready
- `thinking`：Agent思考中
- `outputting`：输出内容中
- `waiting_confirm`：等待用户审批（最高优先级提醒）
- `running`：工具执行中
- `done`：任务正常完成
- `error`：任务异常失败
- `timeout`：审批超时失败
- `rejected`：用户拒绝任务

UI 视觉规则：
- `waiting_confirm`：橙红色主题、呼吸闪烁动画、播放提醒音一次、短震动
- `error / timeout`：红色
- `done`：绿色
- `thinking`：蓝色
- `idle`：灰色
- `outputting / running`：青色

### 2. App → Server（用户操作指令包）
```json
{
  "type": "user_action",
  "action": "approve",
  "remark": ""
}
```
action枚举：
- `approve`：同意
- `reject`：拒绝
- `view_detail`：查看/收起详情
- `stop_task`：强制终止任务
- `clear_alert`：清除提醒音效闪烁

## UniApp 前端UI规范（全屏面板，对标Ulanzi D200H网格）
1. App默认全屏运行，隐藏系统状态栏（尽量沉浸式），深色底色，黑色分割线分隔格子
2. 布局从上到下：
   - 顶部状态栏区域：大号文字展示当前状态名称 + 摘要summary；同时显示WS连接状态（已连接 / 断开）
   - 详情折叠区：默认收起；触发 view_detail 时展开，展示 detail、command、file_path、风险等级
   - 下方固定网格按钮区（5个等大触摸格子）
     ✅ 同意 | ❌ 拒绝 | 📄 查看详情 | ⏹ 终止任务 | 🔊 语音监听开关
3. 交互反馈规则（所有点击动作）
   - 短促提示音效（资源放在UniApp静态资源目录）
   - 安卓震动反馈（短震50ms）
4. 断线逻辑
   - WS断开：顶部提示红色【连接断开】，释放屏幕常亮锁，停止语音监听
   - 自动重连：每2秒尝试重连WS；重连成功后重新上锁常亮，恢复面板状态

## 开发优先级（P0→P3，严格顺序，不要一步写完所有功能）
### P0｜第一版（必须先做完，保证可用闭环）
电脑服务端：
1. Python websockets WS服务，端口8765
2. HTTP POST接口 `/push`，接收Agent推送状态并广播给已连接App
3. 接收App发来的user_action，控制台打印完整日志
4. 提供配置项：Agent回调地址（Hermes / Cursor回调地址，收到审批结果后POST转发）

UniApp安卓端：
1. WS连接、断线自动重连逻辑
2. 连接成功申请屏幕常亮；断开释放
3. 基础UI：状态展示 + 5个触摸按钮网格
4. 按钮点击：发送action、音效、震动
5. 基础状态渲染：不同status切换底色，无复杂动画

### P1｜第二版（体验增强）
1. `waiting_confirm` 呼吸闪烁动画、进场提醒音
2. 详情面板折叠/展开
3. 重连后恢复上一条状态（状态缓存）
4. risk_level风险等级标签可视化：normal / low / high；high风险额外强提醒
5. App内提供设置页：修改电脑IP、端口，不用硬编码WS地址

### P2｜第三版（语音控制，你重点想要的能力）
1. 语音开关按钮：点击开启/关闭麦克风监听
2. 语音关键词映射：
   - “同意” → approve
   - “拒绝” → reject
   - “查看详情” → view_detail
   - “停止任务” → stop_task
3. 语音识别状态UI提示：正在聆听、识别失败、识别成功
> UniApp可以使用：plus.speech（安卓原生语音）优先，不需要依赖浏览器WebSpeech。

### P3｜后续扩展（本次vibe coding不实现，留作后续迭代）
- 审批历史日志本地存储（App内查看历史记录）
- 超时自动拒绝配置
- 多Agent会话区分
- 自定义按钮配置
- 离线语音模型集成（当前先用系统自带语音识别）

## 技术栈约束（强制，不要随意替换）
- 电脑服务端：Python + websockets；仅内置简单HTTP路由用于接收Agent推送，不引入FastAPI、Django等重型框架
- 手机端：UniApp（uVue3），编译为安卓原生APK；**不做网页、PWA、H5网页壳子方案，不使用浏览器承载**
- 通信：仅局域网WebSocket；不做蓝牙、MQTT（简化第一版）
- 鉴权：内网自用，第一版不做登录、加密
- 资源：音效放UniApp static目录，本地加载，不依赖外部网络资源

## 测试接口（电脑端curl调试，方便单独测试面板）
```bash
curl -X POST -H "Content-Type: application/json" -d '{
  "status": "waiting_confirm",
  "summary": "即将执行删除文件操作",
  "detail": "rm /test.txt",
  "extra": {"risk_level":"high","command":"rm /test.txt","file_path":"/test.txt"}
}' [http://127.0.0.1:8765/push](http://127.0.0.1:8765/push)
```

## 非需求（明确禁止实现）
1. ❌ 不做网页/PWA/H5浏览器版本，全部能力落在UniApp原生安卓包
2. ❌ 不做iOS版本，只针对安卓（红米Note11T Pro）
3. ❌ 第一版不做远程外网访问，仅限局域网
4. ❌ 不引入数据库，日志仅电脑控制台输出
5. ❌ 不做复杂UI美化，优先保证功能稳定

## 给Cursor / VibeCoding 的开发提示（AI阅读专用）
> 你在Vibe Coding模式，优先保证代码可运行、注释清晰，严格按P0→P1→P2顺序迭代，不要一次性写完P2/P3。
> 项目分为两大部分：【电脑python服务】、【UniApp项目】。分开输出，文件结构干净。
> UniApp代码遵循uniapp vue3规范，适配安卓打包；优先使用uniapp原生plus能力做常亮、震动、音频、语音，**严禁依赖浏览器API**。
> 完成P0之后，输出清晰指引：
> 1. 电脑端启动命令
> 2. UniApp如何运行/打包APK
> 3. 手机上需要开启的权限、系统设置清单
> 4. curl测试命令
> 5. Hermes Agent、Cursor Agent接入配置方式

---

### 使用方式说明（你自己保存）
1. Cursor新建空项目
2. 新建文件 `agent_confirm_panel_spec.md`，把上面全部内容粘贴进去保存
3. 在Cursor里直接发指令：
```
Read agent_confirm_panel_spec.md，严格按照文档，先开发P0优先级内容。分为电脑Python服务和UniApp Vue3安卓端，不要做网页方案。
```
4. P0跑通、可以双向通信之后，再指令它继续P1，之后P2语音功能。
