// Single source of truth for issue lifecycle labels, timeouts, and messages.
// Borrowed from anthropics/claude-code/scripts/issue-lifecycle.ts and adapted
// for deepgram/cli.

export const lifecycle = [
  {
    label: "invalid",
    days: 3,
    reason: "this doesn't appear to be about the Deepgram CLI",
    nudge:
      "This doesn't appear to be about the [Deepgram CLI](https://github.com/deepgram/cli). For general Deepgram support, visit [developers.deepgram.com](https://developers.deepgram.com).",
  },
  {
    label: "needs-repro",
    days: 7,
    reason: "we still need reproduction steps to investigate",
    nudge:
      "We weren't able to reproduce this. Could you provide steps to trigger the issue — what you ran, what happened, and what you expected?",
  },
  {
    label: "needs-info",
    days: 7,
    reason: "we still need a bit more information to move forward",
    nudge:
      "We need more information to continue investigating. Can you make sure to include your Deepgram CLI version (`dg --version`), OS, and any error messages or logs?",
  },
  {
    label: "stale",
    days: 14,
    reason: "inactive for too long",
    nudge: "This issue has been automatically marked as stale due to inactivity.",
  },
  {
    label: "autoclose",
    days: 14,
    reason: "inactive for too long",
    nudge: "This issue has been marked for automatic closure.",
  },
] as const;

export type LifecycleLabel = (typeof lifecycle)[number]["label"];

export const STALE_UPVOTE_THRESHOLD = 10;
