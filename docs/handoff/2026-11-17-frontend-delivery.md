# Frontend delivery

The bilingual conversation scaffold is packaged for review against release baseline `v1.2.2` (`b885d21`). It runs on synthetic events. The current application routes and server implementation are outside this delivery.

| Artifact | Location |
| --- | --- |
| Preview start and smoke journey | [Preview guide](../../app/static/ui/README.md) |
| Conversation walkthrough | [Conversation entry page](../../app/static/ui/index.html) |
| Previous task preview | [Task entry page](../../app/static/ui/workbench.html) |
| Chat shell, event rendering, and mock stream | [Chat modules](../../app/static/ui/chat/) |
| Earlier task screens and read adapter | [Workbench modules](../../app/static/ui/workbench/) |
| Common DOM and icon helpers | [Shared modules](../../app/static/ui/shared/) |
| Light and dark marks | [Icon assets](../../app/static/ui/assets/) |
| Proposed SSE event contract | [Conversation design](2026-11-17-conversation-ui-design.md) |
| Seven interaction states | [Conversation wireframes](2026-11-17-conversation-ui-wireframes.md) |

The implementation adds an execution pane beside the conversation on wide screens, with a pane switch on narrow screens. The execution pane uses a spanning scope tile, expandable Route A and Route B cards, and compact activity grouped by phase. Approvals remain in the conversation. The sidebar and step cards respect reduced motion. Model configuration is a preview preference, and live credentials belong to the trusted provider process.

The static preview has no build step. Test asset paths with `python -m unittest discover -s tests -p test_ui_preview_assets.py` from the release checkout. Node syntax and synthetic behavior checks cover the mock stream, approval, cancellation, route notes, and disclosure behavior. Browser rendering remains unverified. The live milestone still requires the authenticated entry, agreed SSE and action endpoints, replay and cursor handling, and backend cancellation checks.
