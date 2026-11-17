# BinaryRepair conversation UI — wireframe review

Deliverable 2 follows the approved [information architecture and event contract](2026-11-17-conversation-ui-design.md). All examples use synthetic data. The frames describe the interface before the scaffold is built.

## 1. Idle chat

```text
┌─────────────────────┬────────────────────────────────────────────┐
│ BinaryRepair        │ New chat                              中文 │
│ + New chat          │                                            │
│   Search            │                                            │
│                     │             Start a repair                 │
│ Recent              │       Describe what needs to change.       │
│ Record length  •    │                                            │
│ Header check        │                                            │
│                     │                                            │
│                     │       ┌────────────────────────────┐       │
│                     │       │ Describe the defect…       │       │
│ Settings            │       │ ＋                         ↑ │       │
└─────────────────────┴───────┴────────────────────────────┴───────┘
```

The sidebar uses the same title and status position on every row. The active row has a quiet fill. Clicking a title opens its conversation. The header language switch changes the interface in place and preserves draft text. When intake needs a file, the composer shows an attachment button with a visible filename after selection. The Send arrow has an accessible name and is disabled until there is a valid submission. The ASCII rule appears beside the defect field when it applies.

## 2. Streaming assistant reply

```text
                                             Review the contract.

  Reviewed saved revision 2                              08:00:04
  Thinking…                                                   4 s

  The contract covers the expected record length. I’m checking
  whether the current evidence covers incomplete records. ▏

  ┌────────────────────────────────────────────────────────────┐
  │ Ask a follow-up…                                           │
  │                                         Stop   Queue message│
  └────────────────────────────────────────────────────────────┘
```

Text updates within the same assistant message. Only a real thinking state gets an elapsed indicator, and it becomes a short summary when the server closes that step. Stop requests cancellation. A follow-up submitted while the current turn is active receives a server-confirmed `Queued` label on its own message. The composer keeps its position as the transcript grows. If the reader scrolls up, show one `↓ Latest` action above the composer.

## 3. Expanded tool call

```text
  ▾ Reviewed contract metadata                      08:00:04 · 1 s
    Contract revision 2

    Output                                                   Copy
    ┌──────────────────────────────────────────────────────────┐
    │ Saved contract revision 2.                               │
    └──────────────────────────────────────────────────────────┘
```

The collapsed row shows the action, state, and time. An in-flight tool keeps its slot and gains duration when the result arrives. Use the same disclosure for command output and file changes. Command details show registered display location, stdout or stderr, and the exit code when known. File changes group unified diffs by file with line counts and a **Proposed** or **Applied** label. Show `Show more` only for truncated previews with additional output available. Opening a row keeps its header in view.

## 4. Plan checklist

```text
  ▾ Plan                                             1 of 3 steps
    ✓ Review contract
    ◉ Inspect available evidence
    ○ Summarize checks
```

Keep the checklist in place while statuses change. The markers have accessible labels for completed, in progress, pending, failed, and skipped. Show the count only when the server has supplied a complete plan. It counts completed steps and carries no implication about repair progress. A collapsed plan shows the current step and the count in one line.

## 5. Approval gate

```text
  The contract is ready for review.

  ┌────────────────────────────────────────────────────────────┐
  │ Approve repair contract                          Revision 2│
  │ Scope   Preserve valid records. Reject incomplete records.  │
  │ ▸ Review saved fields and SHA-256                          │
  │                                                            │
  │ [Edit]                            [Deny] [Approve contract] │
  └────────────────────────────────────────────────────────────┘

  Waiting for your approval
```

The card blocks the gated action and remains in the transcript. Approving records the displayed revision. Once confirmed, show `Contract approved` and, when eligible, a separate `Start run` action. Edit opens the reviewed fields in place and a successful save returns a new review. Deny resolves the card with a clear state. If the server reports a conflict, retain local edits, show the current revision, and offer `Review updated contract`.

## 6. Error and retry

```text
  ! Evidence could not be loaded                       08:00:15
    The verification bundle is unavailable.             [Retry]
    ▸ Details
```

The error stays next to the affected activity. Details can show a correlation ID and a user-safe code. If the server has scheduled a retry, show `Retry scheduled` with the supplied time in place of the button. Use `Reconnecting…` in the header for a stream interruption. A cancellation request shows `Cancellation requested` until the server confirms termination. `Termination unconfirmed` is a distinct outcome with the guest reset requirement if one is supplied.

## 7. Run summary

```text
  Verification ended                           18 s · 3 steps

  No verdict
  Required verification evidence was unavailable.

  Route A    V3 not run
  Route B    V3 not run

  ▸ Checks                                      ▸ Evidence
```

The summary appears once the server ends the run. The verdict or its absence is the first result. A supplied verdict is shown verbatim with its reason or limitation nearby. The preview uses `not_run` only where the check status is explicit. Each disclosure exposes its matching checks or artifacts. Include recorded file counts, tokens, or cost in details when available. The summary does not fill missing metrics with zeros.

## Layout and interaction to verify in the scaffold

- Use a sidebar about 15 rem wide, a reading column up to 44 rem wide, and one bottom composer aligned to the transcript. The sidebar and composer are the only surfaces with a material effect. Use solid, higher-contrast surfaces when the browser requests reduced transparency or increased contrast.
- On phones, the sidebar opens from one header button and the reading column uses the available width. Approval actions wrap in order. Keep the composer above the software keyboard and let output blocks scroll independently if needed.
- Give controls immediate press feedback. Keep search, navigation, and keyboard-triggered actions instant. Use a static indicator for thinking under reduced motion. Keep event rows from moving on every text update.
- Display one locale at a time. Review the shipped labels with `New chat / 新建对话`, `Queue message / 加入队列`, `Stop / 停止`, `Approve contract / 批准契约`, and `No verdict / 尚无结论`. Enter during Chinese input composition commits the composition. It never sends the message.
- Restore focus to a disclosure header when it closes. Announce actionable milestones and complete replies to assistive technology. Keep timestamps secondary and give every status a text label.

Next review is the static shell and synthetic event stream. The seven views above define what it should reproduce.
