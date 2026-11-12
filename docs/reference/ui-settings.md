# UI preferences (`br.settings.v1`)

`/settings` stores operator preferences in this browser only. No preference is
sent to the controller; the model key remains server-side.

| Key | Values | Applied as |
|---|---|---|
| `density` | `comfortable` / `compact` | `html[data-density]`; tighter content/stream spacing |
| `fontSize` | `small` / `default` / `large` | `--chat-font-size` for conversation and guide body |
| `codeFont` | 10–16 | `--code-font-size` for code/diff blocks |
| `animations` | boolean | `html[data-motion="off"]` disables transitions/animations |
| `sendKey` | `enter` / `ctrl-enter` | composer keydown handling |
| `autoScroll` | boolean | stream scroll-to-bottom on new messages |
| `taskSort` | `recent` / `name` | sidebar task order (pinned always first) |
| `historySync` | boolean | server GET/PUT for task/draft history; off = local only |
| `defaultWorkspace` | workspace id or `""` | landing initial workspace when no last-selection exists |
| `defaultMode` | mode id or `""` | landing initial mode when no last-selection exists |

## Behavior

- `_settings_boot.html` applies density, motion, chat/code font before first
  paint to avoid a flash.
- `settings.js` exposes `window.BRSettings` (`get`, `save`, `reset`, `apply`)
  and dispatches `brsettings:change`; `jobs-list.js` and `agent.js` consume the
  current values at event time.
- Import accepts only known keys and ignores unknown fields. Export downloads a
  JSON file. Reset removes the key and re-applies defaults.
- “清空本机任务列表” removes `br.jobs.v1`; “清空本机对话缓存” removes every
  `br.msg.v1.*` key. “清空服务端历史” calls `DELETE /api/history` with CSRF and
  is irreversible.
- Theme remains owned by `br.theme` (`light` / `dark` / `system`) so the
  pre-paint theme boot can run independently of the settings file.

## Boundaries

Preferences are per-browser, not per-account. With `historySync: false` the
server may still hold older conversations; turning sync back on reconciles on
the next open. Clearing server history affects every browser using the same
service instance.
