# Task conversation history

The console keeps one conversation per repair task. There are two layers:

| Layer | Where | Purpose |
|---|---|---|
| Browser cache | `localStorage["br.msg.v1.<job-id>"]` | instant replay, offline fallback |
| Server store | `var/gui-history.db` (SQLite) | same task from another browser pointed at this service |

A new draft (`/agent/new` before a job exists) uses the fixed scope `draft`,
so it is also available from another browser on the same service. Entry points
that mean "start over" (`新建修复内容`, `/jobs/new`) carry `?new=1`; the console
clears the local and server draft, removes the flag from the URL, and starts
with an empty conversation.

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/history/<scope>` | scope = job id; returns `{scope, entries, updated_at, storage}` |
| `PUT` | `/api/history/<scope>` | CSRF header required; body `{entries:[...]}` |
| `DELETE` | `/api/history/<scope>` | CSRF header required; used by `?new=1` |

Entries are opaque client state (`message`, `tool`, `repair`). The store
validates the scope (`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`), the entry count
(≤ 120), the entry kind and the serialized size (≤ 512 KiB). It never executes
or interprets the payload.

## Generation guard

Every row has a `generation`. `DELETE` resets the entries and increments it;
`PUT` carries the generation the page loaded. A stale tab (for example, one
that was open before `?new=1` cleared the draft) is rejected with `409` instead
of restoring the old conversation. `GET` returns the current generation.

## Merge on open

1. Read the local cache.
2. Fetch the server copy.
3. Use whichever has the newer last `at` timestamp; on a tie, the longer list.
4. If the local copy is newer (e.g. a save was interrupted), push it to the
   server. On any failure the page keeps the local copy and shows
   “本地暂存”.

The badge next to the task header shows `服务端历史 / 服务端已同步 / 同步中 /
空白草稿 / 草稿同步中 / 草稿已同步 / 本地草稿 / 本地暂存`.

## Cross-device, honestly

The server binds `127.0.0.1` by default. “Cross-device” means another browser
reaching the **same service instance**; exposing that service on a LAN is a
deployment decision with its own authentication and TLS requirements. The UI
copy says “同一服务 … 换浏览器可恢复”, not “公网同步”.

The session token still protects the endpoint; without the `br_session` cookie
the API is unavailable.

## Limits / follow-ups

- Last-writer-wins per task; two tabs editing the same task concurrently can
  overwrite each other.
- No server-side redaction of user-pasted code; the store is inside the local
  data directory and is excluded from git (`var/`).
- `DELETE` is currently used to reset the draft; task deletion still has no
  dedicated UI.
