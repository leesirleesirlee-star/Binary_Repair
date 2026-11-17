# BinaryRepair conversation preview

From the release checkout, run:

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory app/static/ui
```

Open `http://127.0.0.1:8765/index.html`. The earlier preview URL with `?preview=1` also opens the conversation. Stop the static server with Ctrl+C.

Choose **Review a sample contract** or type a message. Open **Execution** to inspect tool output, proposed changes, and the Route A and Route B steps. On a wide screen, Execution stays next to the conversation. The sample pauses for contract review. Approve it, then choose **Start run** to see command output, retry, and a summary without a verdict. While a turn is active, use **Queue message** to add a follow-up or **Stop** to request synthetic cancellation.

The execution overview puts the repair scope above two route tiles. Expand a route to see its stages and the recorded sample note. The event log groups cards by phase. Click or press Enter on a card to reveal its timestamp and any output, diff, or retry action. Durations appear when available. Status text remains visible without color or motion.

Hover over the desktop rail to open it, or use its pin button to keep it open. Keyboard focus also opens the sidebar when navigating the rail. Reduced motion removes the spring transition. Use the header language button for Simplified Chinese. Settings offers system, light, and dark appearance. Search chats with Ctrl+K or Cmd+K. Both icon variants are in [assets](assets/).

**Model configuration** saves a preview model preference only. For the live provider, configure `DEEPSEEK_API_KEY` and `BINARYREPAIR_ASSISTANT_MODEL` in the trusted provider process. There is no browser key entry or live provider call in this preview.

For a Figma prototype, keep the layers named `Sidebar`, `Icon rail`, and `Navigation content` in both component variants. Change the clipped width between variants and preserve the nested layer names so Smart Animate can match them.

This scaffold uses an in-memory synthetic event source. A file selection supplies display metadata only. No binary is processed and no backend or model is contacted. Conversations and transcripts reset on reload while language and appearance preferences persist. SSE resume, backend cancellation, transcript persistence, and large-transcript virtualization are later deliverables.

The previous task-oriented preview is available at `/workbench.html?preview=1`.

The public entry pages stay in this directory. Implementation files are grouped under [chat](chat/), [workbench](workbench/), and [shared](shared/). The [frontend handoff](../../../docs/handoff/2026-11-17-frontend-delivery.md) links the design and verification notes.
