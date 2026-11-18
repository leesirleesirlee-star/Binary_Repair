# Frontend integration for the v1 task list

Follow the [Frontend–Backend Integration Contract v1](frontend-backend-contract-draft-2026-11-17.md). This guide covers session bootstrap and the task collection on the Binary_Repair v1.2.2 integration copy.

## Connect to the GUI origin

The browser uses the GUI origin, normally `http://127.0.0.1:8123` in HTTP mode. Use relative API URLs from a page served on that origin. If the frontend uses a separate development server, configure a same-origin proxy to the GUI and route both the bootstrap and the API through the same browser origin. Keep the host consistent throughout the session because the browser scopes cookies to the host.

1. Open the launcher-provided bootstrap link in the browser. The GUI responds with a `303`, removes the bootstrap token from the URL, and sets the HttpOnly, SameSite=Strict `br_session` cookie. Keep this URL out of frontend source and logs.
2. Call `GET /api/session` with `X-BinaryRepair-Contract-Version: 1`. A valid session receives `{ "session": true, "csrf_token": "…" }`. Keep the CSRF token in memory for future writes. The two GET requests in this guide use the session cookie and require no CSRF header.
3. Call `GET /api/jobs?limit=50` with the same version header. The GUI obtains summaries from the controller on the server side. The browser sends its cookie to the GUI through `credentials: "same-origin"`.

## Request and response

```js
async function getV1(path) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "X-BinaryRepair-Contract-Version": "1",
    },
  });
  if (response.headers.get("X-BinaryRepair-Contract-Version") !== "1") {
    throw new Error("The server did not select contract v1.");
  }
  const body = await response.json();
  return { status: response.status, body };
}

const session = await getV1("/api/session");
if (session.status === 200 && session.body.session === true) {
  const tasks = await getV1("/api/jobs?limit=50");
  // Render tasks.body.jobs only for status 200.
}
```

A successful collection response is `200 {"jobs":[...]}`. This example uses synthetic text.

```json
{
  "jobs": [
    {
      "id": "example-job",
      "defect": "Synthetic task used for interface tests.",
      "phase": "draft",
      "created_at": "2026-11-18T00:00:00+00:00",
      "outcome": null
    }
  ]
}
```

The collection is scoped to the current local workspace. The implementation defaults to 50 records and accepts an integer limit from 1 through 50. Records are ordered newest first. `id` is opaque and `created_at` is UTC. `phase` is a lifecycle stage. `outcome` is the latest recorded terminal acceptance label, or `null` when none is available. Render the defect text as text and preserve the distinction between lifecycle stage and acceptance. An empty workspace returns `200 {"jobs":[]}`.

## Handling failures

V1 responses echo `X-BinaryRepair-Contract-Version: 1`; an unsupported version returns `400 unsupported_contract_version` without an echo. API responses use `Cache-Control: no-store` and `Vary: X-BinaryRepair-Contract-Version`. Errors include `detail`, `code` and `correlation_id`. Use the code to select a localized message.

| Status and code | Frontend handling |
| --- | --- |
| `401 session_required` | Show the session bootstrap entry point. |
| `403 request_forbidden` | Show that the request was rejected. |
| `422 invalid_input` | Check the requested limit. |
| `503 capability_unavailable` | Show that the task list is unavailable and offer retry. |

The current listing implementation uses the HTTP controller. Keep the legacy UI for workflows that still use unversioned routes. The v1 task detail and write routes are outside this handoff.

Backend verification on Windows covered 42 interface and security tests and a live GUI-to-controller HTTP smoke test with a synthetic SQLite record. The frontend application has yet to be exercised against these endpoints.
