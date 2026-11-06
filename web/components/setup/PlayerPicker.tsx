"use client";

import { useState } from "react";
import { SelectFilter, useDebounced } from "@/components/projections/Filters";
import { PositionTag } from "@/components/projections/PlayerBits";
import { api } from "@/lib/api/client";
import { useApi } from "@/lib/api/hooks";
import { describeError } from "@/lib/api/result";
import { POSITIONS, type PlayerIdentity, type PlayerSearchResponse, type Position } from "@/lib/api/types";
import { MAX_TEAM_PLAYERS } from "@/lib/league-form";

/**
 * Typeahead over GET /players (contract v1.3): identity only, never gated.
 * Two characters start a search; results are debounced and capped at 50.
 */
export function PlayerPicker({
  id,
  value,
  onChange,
  error,
  max = MAX_TEAM_PLAYERS,
}: {
  id: string;
  value: PlayerIdentity[];
  onChange: (players: PlayerIdentity[]) => void;
  error?: string;
  max?: number;
}) {
  const [text, setText] = useState("");
  const [position, setPosition] = useState<Position | "">("");
  const q = useDebounced(text.trim(), 300);
  const active = q.length >= 2;
  const { result, loading } = useApi<PlayerSearchResponse>(active ? `players:${q}:${position}` : null, (signal) =>
    api.players.search(q, position || undefined, 50, { signal }),
  );
  const chosen = new Set(value.map((p) => p.player_id));
  const full = value.length >= max;
  const resultsId = `${id}-results`;
  const describedBy = [`${id}-hint`, error ? `${id}-error` : null].filter(Boolean).join(" ");

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-[1fr_11rem]">
        <div>
          <label htmlFor={id} className="mb-1 block text-sm font-medium">
            Add players to your team
          </label>
          <p id={`${id}-hint`} className="mb-1 text-xs text-muted">
            Optional. Type at least two letters of a name, or a team code for a defense.
          </p>
          <input
            id={id}
            type="search"
            className="field"
            value={text}
            maxLength={60}
            autoComplete="off"
            aria-describedby={describedBy}
            aria-invalid={error ? true : undefined}
            aria-controls={resultsId}
            onChange={(e) => setText(e.target.value)}
          />
          {error ? (
            <p id={`${id}-error`} className="mt-1 text-sm font-medium text-danger-ink">
              {error}
            </p>
          ) : null}
        </div>
        <SelectFilter
          label="Position"
          value={position}
          options={[{ value: "" as Position | "", label: "Any position" }, ...POSITIONS.map((p) => ({ value: p as Position | "", label: p }))]}
          onChange={(v) => setPosition(v)}
        />
      </div>

      <div aria-live="polite">
        {value.length ? (
          <ul className="flex flex-wrap gap-2" aria-label="Chosen players">
            {value.map((p) => (
              <li key={p.player_id} className="flex items-center gap-2 rounded-full border border-accent bg-accent-soft py-1 pl-3 pr-1 text-sm">
                <span className="font-medium">{p.name}</span>
                <PositionTag position={p.position} />
                <span className="text-xs text-muted">{p.team ?? "FA"}</span>
                <button
                  type="button"
                  className="btn btn-ghost h-7 min-h-0 w-7 rounded-full p-0"
                  aria-label={`Remove ${p.name}`}
                  onClick={() => onChange(value.filter((x) => x.player_id !== p.player_id))}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">No players added yet. You can add them later too.</p>
        )}
        <p className="mt-1 text-xs text-muted">
          {value.length} of {max}
        </p>
      </div>

      <div id={resultsId}>
        {!active ? null : loading && !result ? (
          <p className="text-sm text-muted" role="status">
            Searching…
          </p>
        ) : result?.kind === "ok" ? (
          result.data.players.length ? (
            <ul className="grid gap-1 sm:grid-cols-2" aria-label="Matching players">
              {result.data.players.map((p) => {
                const isChosen = chosen.has(p.player_id);
                return (
                  <li key={p.player_id}>
                    <button
                      type="button"
                      disabled={isChosen || full}
                      onClick={() => onChange([...value, p])}
                      className="flex w-full items-center justify-between gap-2 rounded-md border border-line bg-surface px-3 py-2 text-left text-sm hover:bg-surface-2 disabled:opacity-50"
                    >
                      <span className="font-medium">
                        {isChosen ? "" : "Add "}
                        {p.name}
                      </span>
                      <span className="flex items-center gap-2 text-xs text-muted">
                        <PositionTag position={p.position} />
                        {p.team ?? "FA"}
                        {isChosen ? " · added" : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-sm text-muted" role="status">
              No players match “{q}”.
            </p>
          )
        ) : result?.kind === "unreachable" ? (
          <p className="text-sm font-medium text-danger-ink" role="status">
            {result.message}
          </p>
        ) : result?.kind === "error" ? (
          <p className="text-sm font-medium text-danger-ink" role="status">
            {describeError(result.code, result.status).title}. {result.message}
          </p>
        ) : null}
        {full ? <p className="mt-1 text-xs text-muted">That is the most players a team can list.</p> : null}
      </div>
    </div>
  );
}
