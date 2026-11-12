# BinaryRepair 工作台 · 从这里开始

这是一个**本地运行的 AI 二进制漏洞修复工作台**。你可以在浏览器里：

- 直接粘贴带漏洞的代码，获得 CWE 标注、修复建议、补丁与差异；
- 按引导创建二进制修复任务，查看证据、验证义务和导出报告；
- 更换主题、密度、字号，搜索/置顶/重命名任务；
- 查看连接器、工具矩阵与完整使用说明。

> 当前是**预览版**：默认使用合成 fixture 演示数据，不执行真实修复；页面上的「演示数据」标签会明确提示。需要真实模型时，在控制台右侧配置 provider key。

## 1. 运行环境

- Python **3.10+**（推荐 3.11）
- macOS / Linux / Windows
- 首次启动需要联网安装 Python 依赖；之后可离线使用

## 2. 启动

### macOS / Linux

```bash
cd BinaryRepair-Workbench
chmod +x start-gui.sh
./start-gui.sh
```

### Windows

双击 `start-gui.bat`，或在 PowerShell 中：

```powershell
cd BinaryRepair-Workbench
.\start-gui.ps1
```

脚本会自动创建 `.venv`、安装 `requirements.txt`、生成本地会话 token，然后启动服务。

## 3. 打开

终端会打印类似：

```text
http://127.0.0.1:8123/?token=...
```

把这个**完整地址**复制到浏览器打开。token 只用于本机会话，不要发到公开群。

## 4. 第一次使用建议

1. 打开后先点左侧「使用说明」，花 3 分钟浏览完整手册。
2. 在「新建修复内容」中粘贴一段代码，例如：

```python
import subprocess
subprocess.check_output(cmd, shell=True)
```

3. 查看修复卡：问题 / CWE / 影响 / 修复 / 警醒 / 参考。
4. 点右上角「设置」调整主题、密度、发送键；设置只保存在本浏览器。
5. 想体验完整工作台，在工作台启动卡切换到「演示会话」，再选成功/失败场景。
6. 界面支持九种语言：侧栏「语言」或右上角切换；技术证据保持原文。

## 5. 常见问题

| 问题 | 处理 |
|---|---|
| `python3: command not found` | 安装 Python 3.10+，或把 Python 加入 PATH |
| `No module named ...` | 删除 `.venv` 后重新运行启动脚本 |
| 端口 8123 被占用 | 设置 `BINARYREPAIR_PORT=8124` 后重启 |
| 浏览器 401 | 重新打开终端打印的带 `?token=` 的完整地址 |
| 页面没有样式 | 强制刷新（Cmd/Ctrl + Shift + R） |
| 想清空演示数据 | 重启服务；或在设置中清空本机/服务端历史 |

## 6. 边界

- 默认不执行上传或修复后的二进制；静态建议不构成验收结论。
- 插件清单是只读 JSON，不会被加载执行。
- 连接器“已就绪”只表示本机具备条件，不代表远端已连通。
- 需要真实模型 key 时，key 只保存在本机服务端，不回显、不写入日志。

更多内容：运行后访问 `/guide`，或查看 `docs/showcase/` 截图。
