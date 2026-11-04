"use client";

import { useState } from "react";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/result";
import type { League } from "@/lib/api/types";

function formatDate(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unknown" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function LeagueList({
  leagues,
  currentId,
  onUse,
  onChanged,
  onDeleted,
}: {
  leagues: League[];
  currentId: string | null;
  onUse: (id: string) => void;
  onChanged: (league: League) => void;
  onDeleted: (id: string) => void;
}) {
  return (
    <ul className="grid gap-3" data-testid="league-list">
      {leagues.map((l) => (
        <LeagueRow key={l.id} league={l} current={l.id === currentId} onUse={onUse} onChanged={onChanged} onDeleted={onDeleted} />
      ))}
    </ul>
  );
}

function LeagueRow({
  league,
  current,
  onUse,
  onChanged,
  onDeleted,
}: {
  league: League;
  current: boolean;
  onUse: (id: string) => void;
  onChanged: (league: League) => void;
  onDeleted: (id: string) => void;
}) {
  const [busy, setBusy] = useState<"sync" | "delete" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  async function sync() {
    setBusy("sync");
    setMessage(null);
    const r = await api.leagues.sync(league.id);
    setBusy(null);
    if (r.kind === "ok") {
      setMessage({ kind: "ok", text: "Rosters re-read from Sleeper." });
      onChanged(r.data);
    } else if (r.kind === "error") setMessage({ kind: "error", text: `${describeError(r.code, r.status).title}. ${r.message}` });
    else if (r.kind === "unreachable") setMessage({ kind: "error", text: r.message });
  }

  async function remove() {
    setBusy("delete");
    setMessage(null);
    const r = await api.leagues.remove(league.id);
    setBusy(null);
    setConfirming(false);
    if (r.kind === "ok") onDeleted(league.id);
    else if (r.kind === "error") setMessage({ kind: "error", text: `${describeError(r.code, r.status).title}. ${r.message}` });
    else if (r.kind === "unreachable") setMessage({ kind: "error", text: r.message });
  }

  return (
    <li className={`card p-4 ${current ? "border-accent" : ""}`} data-testid="league-row">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{league.name}</span>
            <span className="rounded-full border border-line bg-surface-2 px-2 py-0.5 text-xs font-semibold">
              {league.source === "sleeper" ? "Sleeper" : "Manual"}
            </span>
            {current ? (
              <span className="rounded-full border border-accent bg-accent-soft px-2 py-0.5 text-xs font-semibold">In use</span>
            ) : null}
          </p>
          <p className="text-sm text-muted">
            {league.season} · {league.num_teams} teams · {league.scoring.preset_like ? league.scoring.preset_like.toUpperCase() : "custom"} scoring
            {league.source === "sleeper" ? ` · synced ${formatDate(league.last_synced_at)}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {!current ? (
            <button type="button" className="btn btn-primary" onClick={() => onUse(league.id)}>
              Use this league
            </button>
          ) : null}
          {league.source === "sleeper" ? (
            <button type="button" className="btn btn-secondary" onClick={sync} disabled={busy !== null}>
              {busy === "sync" ? "Syncing…" : "Sync rosters"}
            </button>
          ) : null}
          {confirming ? (
            <>
              <button type="button" className="btn btn-secondary text-danger-ink" onClick={remove} disabled={busy !== null}>
                {busy === "delete" ? "Deleting…" : `Delete ${league.name}`}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>
                Keep it
              </button>
            </>
          ) : (
            <button type="button" className="btn btn-ghost" onClick={() => setConfirming(true)}>
              Delete
            </button>
          )}
        </div>
      </div>
      <p role="status" className={`mt-2 text-sm ${message?.kind === "error" ? "font-medium text-danger-ink" : "text-muted"}`}>
        {message?.text}
      </p>
    </li>
  );
}
