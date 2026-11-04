"use client";

import { LoadingState } from "@/components/states/States";
import { api } from "@/lib/api/client";
import type { AttributionResponse } from "@/lib/api/types";
import { ATTRIBUTION_FALLBACK } from "@/lib/content";
import { Section, SourceNote } from "./AboutShell";
import { useMetaContent } from "./useMetaContent";

function safeUrl(url: string | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

export function AttributionView() {
  const { data, source, reason, reload } = useMetaContent<AttributionResponse>(
    "meta:attribution",
    (signal) => api.meta.attribution({ signal }),
    ATTRIBUTION_FALLBACK,
    (d) => !Array.isArray(d.sources) || d.sources.length === 0,
  );

  if (!data) return <LoadingState label="Loading attribution" />;

  return (
    <div className="space-y-8">
      <SourceNote source={source} reason={reason} onRetry={reload} />

      <Section title="Data sources" id="attr-sources">
        <ul className="grid gap-4 md:grid-cols-2">
          {data.sources.map((s) => {
            const href = safeUrl(s.url);
            return (
              <li key={s.id} className="card flex flex-col gap-3 p-5" data-testid={`source-${s.id}`}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <h3 className="text-lg font-semibold">{s.name}</h3>
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${
                      s.in_use ? "border-line bg-accent-soft text-ink" : "border-line bg-surface-2 text-muted"
                    }`}
                  >
                    {s.in_use ? "In use" : "Not in use"}
                  </span>
                </div>
                <blockquote className="border-l-4 border-accent pl-3 font-medium leading-relaxed">{s.text}</blockquote>
                <dl className="grid gap-1 text-sm">
                  <div className="flex flex-wrap gap-x-2">
                    <dt className="text-muted">License:</dt>
                    <dd>{s.license}</dd>
                  </div>
                  {s.used_for ? (
                    <div className="flex flex-wrap gap-x-2">
                      <dt className="text-muted">Used for:</dt>
                      <dd>{s.used_for}</dd>
                    </div>
                  ) : null}
                </dl>
                {s.notes?.length ? (
                  <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
                    {s.notes.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                ) : null}
                {href ? (
                  <p className="mt-auto text-sm">
                    <a href={href} className="link break-all" rel="noopener noreferrer" target="_blank">
                      {href.replace(/^https?:\/\//, "").replace(/\/$/, "")}
                      <span className="sr-only"> (opens in a new tab)</span>
                    </a>
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      </Section>

      <Section title="Use and independence" id="attr-use">
        <ul className="max-w-3xl list-disc space-y-1.5 pl-5 leading-relaxed">
          {data.usage ? <li>{data.usage}</li> : null}
          {data.not_displayed ? <li>{data.not_displayed}</li> : null}
          {data.independence ? <li>{data.independence}</li> : null}
          <li>Nothing in this app places bets or presents any outcome as certain.</li>
        </ul>
      </Section>
    </div>
  );
}
