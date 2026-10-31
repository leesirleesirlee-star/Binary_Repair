# Route B：recovered C 与 unchanged round trip 实验手册

本目录只做一件事：回答技术设计里的待验证问题 **"Can the unchanged recovered-C round trip
and added-code mapping pass their required checks?"**（TechDesign 第 16 节，问题 7）。

答案必须是实验记录，不是判断。每条结论都要带上：二进制 hash、Ghidra 版本、编译命令、
Patcherex2 版本、6 个 oracle 用例的实际输出。

---

## 0. 固定样本与契约（先冻结，再动手）

| 项 | 值 |
|---|---|
| 二进制 | `fixture/vuln`，由 `fixture/build.sh` 从 `vuln.c` 编译 |
| 编译 | `-O0 -g0 -fno-stack-protector -fno-builtin -fno-pie -no-pie` |
| 目标函数 | `copy_name(char *dst, const char *src, unsigned int n)` |
| 有效输入 | 0–15 字节，原样复制，`guard=ok`，退出码 0 |
| 无效输入 | ≥16 字节，必须 `return -1` 且完全不写目标缓冲区 |
| 溢出检测 | `struct record` 里 `name[16]` 后紧跟 `guard` canary（0xDEADBEEF） |
| 判定工具 | `verify.py`（vulnerable / fixed / roundtrip 三种模式） |

先跑一遍原始二进制，确认漏洞真的可复现：

```bash
cd routeb
./fixture/build.sh
python3 verify.py --binary fixture/vuln --mode vulnerable
# 6/6 passed：4 个有效输入正常，2 个超长输入 guard=CORRUPT
```

这一步失败（例如 16 字节没有触发 CORRUPT），先修 fixture，不要继续。

---

## 1. 用 Ghidra 导出函数

```bash
export GHIDRA_HOME=$HOME/ghidra_11.4_PUBLIC   # 按实际目录改
./ghidra/export.sh fixture/vuln copy_name
```

产物：

- `out/copy_name.c`：**原始反编译输出，只读证据，不要直接改**
- `out/copy_name.c.meta.txt`：入口地址、字节数、签名、调用约定、被调函数

检查 meta：`size_bytes` 应为一个很小的数（几十字节），`callees=` 应为空。
若 `NOT FOUND`，说明符号被 strip 或名字不同；用 Ghidra GUI 打开确认函数名，
或先用 `nm fixture/vuln | grep copy_name` 查符号。

**今天要交的观察**：把原始输出贴进实验记录，并列出"为了能编译，我改了哪几处"。

---

## 2. 归一化（normalize），不改语义

新建 `out/copy_name.normalized.c`，只做类型/名字层面的修改：

| Ghidra 常见输出 | 归一化后 | 为什么安全 |
|---|---|---|
| `undefined4` / `undefined8` | `int` / `unsigned int` | 只是宽度明确的类型名 |
| `param_1, param_2, param_3` | `dst, src, n` | 名字，必须与二进制签名一致 |
| `uVar1`, `local_10` | `i` | 局部变量重命名 |
| `(char *)0x...` | 不动或改为具名常量 | 地址常量不要凭空改 |
| `code *`、`__return_storage_ptr__` | 本样本不应出现；出现则记为 blocker | 结构体返回/间接调用超出 MVP 范围 |

规则：

1. **只改类型和名字，不改控制流、不改数组下标、不加检查。**
2. 每改一处，在实验记录里写一行原因。
3. 归一化后的文件单独保存，不要覆盖原始输出。
4. 参考实现放在 `experiment/samples/normalized_reference.c`，**先自己做，再对照**。

编译自检（这一步只证明 C 语法和接口成立，不证明回填成功）：

```bash
cc -O0 -g0 -fno-stack-protector -fno-builtin -c out/copy_name.normalized.c -o /tmp/copy_name.o
```

---

## 3. unchanged round trip（关键 gate）

把**未修复**的归一化 C 重新编译并回填到原 ELF 的副本，然后要求它和原二进制行为完全一致。

使用 Patcherex2（先按文档复现它的 add 示例）：

```bash
python3 -m venv .venv-patcherex
.venv-patcherex/bin/pip install patcherex2

# 3a. 先证明工具链本身能用（文档示例：加法变乘法，5 -> 6）
.venv-patcherex/bin/python probe_patcherex2.py --demo

# 3b. 未修改的 recovered C 回填
.venv-patcherex/bin/python probe_patcherex2.py \
    --mode roundtrip \
    --c-source out/copy_name.normalized.c \
    --out out/vuln_roundtrip

# 3c. 与原始二进制逐用例比较（stdout + 退出码）
python3 verify.py --binary out/vuln_roundtrip --reference fixture/vuln --mode roundtrip
```

**通过标准（全部满足才算 round trip 成立）：**

- [ ] 6/6 用例的 stdout 与退出码和原二进制逐字节相同
- [ ] 16/17 字节输入仍然 `guard=CORRUPT`（缺陷没有被"顺手修好"）
- [ ] `readelf -lW` 的段数量/权限没有意外变化；若有新增段，记录它
- [ ] 改动字节范围可解释（函数体 ± 重定位），并写入 edit manifest
- [ ] Patcherex2 版本、编译器版本、命令都记录

**失败时的处理：**

| 现象 | 含义 | 动作 |
|---|---|---|
| 回填后程序崩溃/不启动 | 重定位、入口、段权限有问题 | 记 `unsupported`，保留候选与日志，换方案前先定位 |
| 有效输入输出不同 | 归一化改变了语义 | 回到第 2 步，逐行 diff |
| 16 字节不再 CORRUPT | 重编译改变了循环/写入 | **不是修复成功**，是行为变化；记为 round-trip failure |
| Patcherex2 装不上/不支持该 ELF | 后端不可用 | 记录版本与报错；这是"unsupported"，不是"没做" |

> round trip 通过只证明"在这个测试域内行为一致"，不证明函数等价。汇报时必须带这句限定。

---

## 4. 结构化修复提案（DeepSeek）

round trip 通过后，才允许让模型改这个函数。

```bash
cd ..
.venv/bin/python scripts/demo_candidate.py \
    --recovered routeb/out/copy_name.normalized.c \
    --out-c routeb/out/copy_name.model.c
```

输出是一个通过 schema 校验的 candidate（`replacement_c` 为完整函数）。

然后：

```bash
.venv-patcherex/bin/python routeb/probe_patcherex2.py \
    --mode fixed --c-source routeb/out/copy_name.model.c --out routeb/out/vuln_fixed

python3 routeb/verify.py --binary routeb/out/vuln_fixed --mode fixed
```

只有 `verify.py --mode fixed` 6/6 通过，才能说"这个候选在测试域内修复了缺陷"。
**编译成功、模型说"已修复"、Patcherex2 没报错，都不算。**

---

## 5. 实验记录模板（直接抄进汇报）

```markdown
### Route B feasibility — <date>

- Host / OS / arch:
- Ghidra version / JDK:
- Fixture sha256:
- Recovered function size:
- Normalization edits (count + one line each):
- Patcherex2 version:
- Round trip: PASS / FAIL / UNSUPPORTED — evidence: <verify json path>
- Defect still observable after round trip: yes / no
- Model candidate: accepted_by_schema / rejected:<reason>
- Fixed candidate: 6/6 pass / fail:<case>
- Layout changes: <segments added? bytes changed?>
- Blockers:
- What I would try next:
```

---

## 6. 明确不做的事（MVP 边界）

- 不恢复整个程序，只恢复被修复的那个函数。
- 不支持 PIE/共享库/异常/C++/多线程/TLS 的样本。
- 不手写重定位引擎；Patcherex2 不成就记 unsupported，等团队决定。
- 不把"编译通过"或"没崩溃"写进证据。
