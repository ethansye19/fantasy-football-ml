"use client";

import { LoadingState } from "@/components/states/States";
import { api } from "@/lib/api/client";
import type { ModelCardResponse } from "@/lib/api/types";
import { APP_LIMITATIONS, MODEL_CARD_FALLBACK } from "@/lib/content";
import { Section, SourceNote } from "./AboutShell";
import { useMetaContent } from "./useMetaContent";

export function LimitationsView() {
  const { data, source, reason, reload } = useMetaContent<ModelCardResponse>(
    "meta:model-card",
    (signal) => api.meta.modelCard({ signal }),
    MODEL_CARD_FALLBACK,
    (d) => !d.limitations?.length,
  );

  return (
    <div className="space-y-8">
      {data ? <SourceNote source={source} reason={reason} onRetry={reload} /> : null}
      <Section title="Model" id="lim-model">
        {data ? (
          <ul className="max-w-3xl list-disc space-y-1.5 pl-5 leading-relaxed" data-testid="model-limitations">
            {(data.limitations ?? []).map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        ) : (
          <LoadingState rows={3} label="Loading model limitations" />
        )}
      </Section>

      <Section title="App" id="lim-app">
        <ul className="grid max-w-4xl gap-3 sm:grid-cols-2">
          {APP_LIMITATIONS.map((l) => (
            <li key={l.title} className="card p-4">
              <h3 className="font-semibold">{l.title}</h3>
              <p className="mt-1 text-sm leading-relaxed text-muted">{l.text}</p>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
