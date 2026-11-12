# 用微信分享给组员

## 方案 A：发一个可点的链接（推荐，组员无需安装）

> 注意：链接指向的是**演示实例**，只使用合成 fixture，不含真实模型 key、真实任务或上传样本。

### A1. 同一 Wi-Fi / 局域网

在项目目录运行一个专用演示实例：

```bash
cd BinaryRepair-Workbench
BINARYREPAIR_BACKEND=mock \
BINARYREPAIR_DATA_DIR=/tmp/binaryrepair-demo \
BINARYREPAIR_SESSION_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
BINARYREPAIR_ALLOWED_HOSTS="$(ipconfig getifaddr en0)" \
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8124
```

把 `http://<你的局域网IP>:8124/?token=<token>` 发到微信群。
macOS 首次可能弹防火墙允许提示；组员需在同一 Wi-Fi。

### A2. 外网临时链接（ngrok）

```bash
ngrok http 8124 --host-header=127.0.0.1
```

把 ngrok 给出的 `https://...ngrok-free.dev/?token=<token>` 发到群里。
组员第一次打开先点 ngrok 的 **Visit Site**；随后会正常进入工作台。

### A3. 只发 ZIP

把 `BinaryRepair-Workbench-*.zip` 发到微信群；组员解压后按 `START-HERE.md`
运行。这是最安全、可长期保留的方式。

## 方案 B：发 ZIP + 启动说明（推荐给需要本地使用的组员）

1. 微信电脑版点「文件」→ 选择 ZIP。
2. 附上下面的消息。
3. 组员解压，macOS/Linux 运行 `./start-gui.sh`，Windows 双击
   `start-gui.bat`，浏览器打开终端打印的带 token 地址。

## 一段可直接粘贴的群消息

```text
【BinaryRepair 工作台 · 体验】
这是一个本地运行的 AI 二进制漏洞修复工作台：可以粘贴代码直接看
CWE 风险、修复补丁与差异，也可以按引导创建二进制修复任务。

在线体验（演示数据，不执行真实修复）：
<把链接放这里>

本地包（推荐，可离线）：
<把 zip 拖进群>

使用步骤：
1) 解压后 macOS/Linux 运行 ./start-gui.sh；Windows 双击 start-gui.bat
2) 浏览器打开终端里带 ?token= 的完整地址
3) 左侧「使用说明」有完整手册；右上角「设置」可改主题/字号/发送键

说明：演示模式使用合成 fixture，不构成「补丁已验证」的证据；
真实模型 key 只保存在各自本机服务端，不会随包分发。
```

## 安全清单

- 不要在公开群发**本机 8123**的 token；为分享单独启动 8124 演示实例。
- 演示实例使用独立 `BINARYREPAIR_DATA_DIR`，避免带出本机历史。
- 不要把真实 provider key、真实样本、真实任务历史打进 ZIP。
- 临时 ngrok 链接用完就关；长期分享请用 ZIP。
- 微信里不要把 token 和公网链接同时转发到不受控的大群。
