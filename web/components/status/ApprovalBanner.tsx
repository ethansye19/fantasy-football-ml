"use client";

import Link from "next/link";
import { useStatus } from "./StatusProvider";
import { testDataLabel } from "@/lib/season";
import { NotApprovedPill } from "./NotApprovedPill";
import { TestDataLabel } from "./TestDataLabel";

/** Split "First sentence. The rest" so the first sentence can be bold. */
export function splitLead(message: string): { lead: string; rest: string } {
  const m = /^(.+?[.!?])\s+(.*)$/s.exec(message.trim());
  return m ? { lead: m[1] ?? message, rest: m[2] ?? "" } : { lead: message.trim(), rest: "" };
}

/**
 * App-wide model status, driven by /meta/approval and by the approval
 * envelope and header of every API response (see approval-mode.ts).
 * Shows nothing when a model is approved; never claims approval itself.
 */
export function ApprovalBanner() {
  const { approval, refresh } = useStatus();

  if (approval.kind === "loading" || approval.kind === "approved") return null;

  if (approval.kind === "not_approved") {
    const testData = approval.serving ? testDataLabel(approval.run) : null;
    return (
      <div
        role="region"
        aria-label="Model status"
        data-testid="approval-banner"
        data-state="not-approved"
        className="sticky top-0 z-30 border-b-2 border-warn-line bg-warn-bg text-warn-ink"
      >
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
          <NotApprovedPill label={approval.label} />
          <p className="order-3 basis-full sm:order-2 sm:min-w-0 sm:flex-1 sm:basis-0">
            <strong>Private test environment only, not a recommendation.</strong>{" "}
            <span className={approval.serving ? "hidden sm:inline" : undefined}>
              {approval.serving
                ? "These numbers come from a model that has not passed its pre-registered test."
                : approval.message}
            </span>
          </p>
          <Link
            href="/about/model-card"
            className="order-2 ml-auto font-semibold underline underline-offset-2 sm:order-3 sm:ml-0"
          >
            Model status
          </Link>
          {testData ? <TestDataLabel label={testData} className="order-4 basis-full" /> : null}
        </div>
      </div>
    );
  }

  if (approval.kind === "no_approved_model") {
    const { lead, rest } = splitLead(approval.message);
    return (
      <div
        role="region"
        aria-label="Model status"
        data-testid="approval-banner"
        data-state="no-approved-model"
        className="border-b border-info-line bg-info-bg text-info-ink"
      >
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
          <p className="min-w-0 flex-1">
            <strong>{lead}</strong> {rest} League setup and the information pages work as usual.
          </p>
          <Link href="/about/model-card" className="font-semibold underline underline-offset-2">
            Why
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div
      role="region"
      aria-label="Model status"
      data-testid="approval-banner"
      data-state="unavailable"
      className="border-b border-line bg-surface-2 text-ink"
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
        <p className="min-w-0 flex-1">
          <strong>Model status unavailable.</strong> {approval.message} Projection features are treated as off.
        </p>
        <button type="button" className="font-semibold underline underline-offset-2" onClick={refresh}>
          Check again
        </button>
      </div>
    </div>
  );
}
