"use client";

import Link from "next/link";
import { LoadingState } from "@/components/states/States";
import { NotApprovedPill } from "@/components/status/NotApprovedPill";
import { api } from "@/lib/api/client";
import type { ModelCardResponse } from "@/lib/api/types";
import { MODEL_CARD_FALLBACK } from "@/lib/content";
import { Section, SourceNote } from "./AboutShell";
import { useMetaContent } from "./useMetaContent";

/**
 * Registry status words (contract v1.3) in plain language. The API never
 * sends the bare word "approved"; an older registry value of "approved" is
 * shown as awaiting release, so this page never claims approval on its own.
 */
export function registryStatusLabel(status: string | null | undefined, serving = false): string {
  switch (status) {
    case "candidate":
      return "Candidate, under test";
    case "rejected":
      return "Rejected: did not pass its test";
    case "retired":
      return "Retired";
    case "awaiting_release":
    case "approved":
      return "Awaiting release";
    case "live":
      return serving ? "Live, serving projections" : "Live";
    default:
      return status ? status.replace(/_/g, " ") : "Unknown";
  }
}

export function ModelCardView() {
  const { data, source, reason, reload } = useMetaContent<ModelCardResponse>(
    "meta:model-card",
    (signal) => api.meta.modelCard({ signal }),
    MODEL_CARD_FALLBACK,
    (d) => !d.status_summary && !d.what_it_predicts,
  );

  if (!data) return <LoadingState label="Loading the model card" />;

  const apiApproved = source === "api" && data.approval_status === "approved" && data.environment === "production";
  const definitions = Object.entries(data.definitions ?? {});

  return (
    <div className="space-y-8">
      <SourceNote source={source} reason={reason} onRetry={reload} />

      <section aria-labelledby="mc-status" className="card space-y-3 p-5" data-testid="model-status">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="mc-status" className="text-lg font-semibold">
            Status
          </h2>
          {apiApproved ? (
            <span className="rounded-full border border-line bg-accent-soft px-2.5 py-0.5 text-xs font-bold uppercase tracking-wide">
              {data.label ?? data.approval_status}
            </span>
          ) : (
            <NotApprovedPill label={data.label} />
          )}
        </div>
        {data.status_summary ? <p className="leading-relaxed">{data.status_summary}</p> : null}
        {data.notice ? (
          <p className="rounded-md border border-warn-line bg-warn-bg px-3 py-2 text-sm text-warn-ink">{data.notice}</p>
        ) : null}
      </section>

      {data.title || data.what_it_predicts ? (
        <Section title="What it predicts" id="mc-predicts">
          {data.title ? <p className="font-medium">{data.title}</p> : null}
          {data.what_it_predicts ? <p className="max-w-3xl leading-relaxed">{data.what_it_predicts}</p> : null}
        </Section>
      ) : null}

      {data.method?.length ? (
        <Section title="How it works" id="mc-method">
          <ol className="max-w-3xl list-decimal space-y-2 pl-5 leading-relaxed marker:text-muted">
            {data.method.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ol>
        </Section>
      ) : null}

      {definitions.length ? (
        <Section title="What the numbers mean" id="mc-definitions">
          <dl className="grid max-w-3xl gap-x-6 gap-y-2 sm:grid-cols-[8rem_1fr]">
            {definitions.map(([term, text]) => (
              <div key={term} className="contents">
                <dt className="font-semibold capitalize">{term}</dt>
                <dd className="mb-2 text-muted sm:mb-0">{text}</dd>
              </div>
            ))}
          </dl>
        </Section>
      ) : null}

      {data.limitations?.length ? (
        <Section title="Known limitations" id="mc-limitations">
          <ul className="max-w-3xl list-disc space-y-1.5 pl-5 leading-relaxed">
            {data.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
          <p>
            <Link href="/about/limitations" className="link">
              All limitations of the app
            </Link>
          </p>
        </Section>
      ) : null}

      {data.models?.length ? (
        <Section title="Model registry" id="mc-registry">
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  <th scope="col">Version</th>
                  <th scope="col">Status</th>
                  <th scope="col">Created</th>
                </tr>
              </thead>
              <tbody>
                {data.models.map((m) => (
                  <tr key={`${m.name}-${m.version}`}>
                    <td>{m.name ?? "Unnamed"}</td>
                    <td className="break-all font-mono text-xs">{m.version ?? "n/a"}</td>
                    <td>{registryStatusLabel(m.registry_status, m.serving === true)}</td>
                    <td className="num whitespace-nowrap">{m.created_at ? m.created_at.slice(0, 10) : "n/a"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}

      {data.documentation?.length ? (
        <Section title="Documentation" id="mc-docs">
          <p className="text-muted">The full evaluation reports and the decision log live in the project repository:</p>
          <ul className="list-disc space-y-1 pl-5">
            {data.documentation.map((d) => (
              <li key={d} className="font-mono text-sm">
                {d}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}
