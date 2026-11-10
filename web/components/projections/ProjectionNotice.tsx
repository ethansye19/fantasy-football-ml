"use client";

import { NotApprovedPill } from "@/components/status/NotApprovedPill";
import { TestDataLabel } from "@/components/status/TestDataLabel";
import type { ApprovalObservation } from "@/lib/api/result";
import type { RunInfo } from "@/lib/api/types";
import { shortDate } from "@/lib/format";
import { testDataLabel } from "@/lib/season";

const SOURCE_LABEL: Record<string, string> = {
  published: "Published run",
  private_test: "Private test run",
  replay: "Historical replay",
  fixture: "Fixture data",
};

function RunLine({ run }: { run: RunInfo }) {
  return (
    <p className="text-xs opacity-90" data-testid="run-line">
      {SOURCE_LABEL[run.source] ?? run.source}: {run.season} week {run.week} · model {run.model_version} ·{" "}
      {run.n_sims.toLocaleString()} simulations · written {shortDate(run.written_at)}
    </p>
  );
}

/**
 * The label and notices every projection-derived view shows (D-099).
 * Not approved: a prominent block with the API's label and every notice.
 * Approved (production only): a quiet run line.
 */
export function ProjectionNotice({ approval }: { approval: ApprovalObservation | null }) {
  // Projection responses always carry an envelope; a missing one counts as not approved.
  const a: ApprovalObservation = approval ?? {
    approved: false,
    status: "not approved",
    label: "not approved",
    environment: null,
    header: null,
    run: null,
    notices: [],
  };

  if (a.approved) {
    return a.run ? (
      <div className="mb-4 text-muted" data-testid="projection-notice" data-approval="approved">
        <RunLine run={a.run} />
      </div>
    ) : null;
  }

  const testData = testDataLabel(a.run);
  const notices = (a.notices.length
    ? a.notices
    : ["Not approved: these numbers come from a model that has not passed its pre-registered test. Private test environment only; not a recommendation."]
  ).filter((n) => n !== testData);

  return (
    <section
      aria-label="Not approved"
      data-testid="projection-notice"
      data-approval="not-approved"
      className="mb-5 rounded-xl border-2 border-warn-line bg-warn-bg p-4 text-warn-ink"
    >
      <div className="flex flex-wrap items-center gap-2">
        <NotApprovedPill label={a.label} />
        <p className="text-sm font-semibold">Private test environment only, not a recommendation.</p>
      </div>
      {testData ? <TestDataLabel label={testData} className="mt-2" /> : null}
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
        {notices.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
      {a.run ? (
        <div className="mt-2">
          <RunLine run={a.run} />
        </div>
      ) : null}
    </section>
  );
}
