# Inline code repair

The console accepts a pasted snippet the same way it accepts a defect
description. A repair is advice, not an acceptance verdict: it never creates a
job, writes evidence or claims that a binary was fixed.

## Flow

```
composer ──▶ extractCode() ──▶ POST /api/code/repair ──▶ repair card
                  │                     │
                  │                     ├─ provider key?  → model patch, validated
                  │                     └─ no key / bad answer → deterministic rules
                  └─ fenced ```lang block, or a code-like multi-line message
```

`extractCode` prefers fenced blocks (the first info string is the language
hint). Otherwise a message with at least three code signals (imports/defs,
braces/semicolons, `subprocess.`, `strcpy(`, ...) is treated as code.

## Rules (`mode: "rule"`)

| Language | Rule | Action |
|---|---|---|
| Python | `subprocess.*` + `shell=True` | `shlex.split(arg)` + `shell=False`, adds `import shlex` |
| Python | `app.run(debug=True)` | `debug=False` |
| Python | `yaml.load(` without `Loader=` | `yaml.safe_load(` |
| Python | `eval` / `exec` / `pickle.loads` | finding only |
| C | `strcpy(dst, src)` | `snprintf(dst, sizeof(dst), "%s", src)` |
| C | `sprintf(dst, ...)` | `snprintf(dst, sizeof(dst), ...)` |
| C | `gets(buf)` | `fgets(buf, sizeof(buf), stdin)` |
| JS | `eval` / `innerHTML` / `child_process.exec` | finding only |
| any | hardcoded secret literal | finding only, evidence masked |

Rewrites carry a residual-risk note: `shell=False` does not block executing a
different program, and `sizeof` is only correct for an array target.

## Model path (`mode: "llm"`)

`repair_code(..., client=client_from_env())` asks the configured provider for
one JSON object (`language`, `title`, `summary`, `confidence`, `findings`,
`patched_code`, `notes`). The answer is parsed and bounded; any transport,
JSON or schema failure silently falls back to the rule path and appends a note.
The browser never sees the key.

## Result shape

```json
{
  "language": "python",
  "title": "命令通过 shell 执行（命令注入风险）",
  "summary": "发现 2 处问题，并生成了补丁。",
  "mode": "rule",
  "model": null,
  "confidence": "high",
  "findings": [{"rule": "py.shell-true", "title": "...", "severity": "high",
                "line": 9, "evidence": "...", "advice": "..."}],
  "patched_code": "...",
  "changed": true,
  "diff": [{"kind": "ctx|add|del|hunk", "text": "..."}],
  "notes": ["..."]
}
```

## Boundary

- The submitted code is **never executed**; there is no import, subprocess or
  eval of user input in `code_repair.py`.
- Without a provider key the snippet stays in this process (rules only). With a
  key configured, the snippet is sent to that provider for the model patch; the
  page labels the result "模型建议" so the difference is visible.
- `POST /api/code/repair` requires the session cookie and the CSRF header.
- The result is rendered with `textContent` (no HTML injection) and stored in
  the browser's conversation history only; no server-side job record.
- Copying a diff or patch is a local clipboard action.
