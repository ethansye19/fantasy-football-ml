"use client";

import { useId, useState, type FormEvent } from "react";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/result";
import type { League } from "@/lib/api/types";

/** Choose which roster is "my team" (PUT /leagues/{id}/my-team). */
export function MyTeamPicker({
  league,
  onSaved,
  submitLabel = "Save my team",
}: {
  league: League;
  onSaved: (league: League) => void;
  submitLabel?: string;
}) {
  const teams = league.teams ?? [];
  const initial = league.my_team_id ?? teams.find((t) => t.is_me)?.id ?? null;
  const [choice, setChoice] = useState<string | null>(initial);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const name = useId();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!choice) {
      setMessage({ kind: "error", text: "Choose your team." });
      return;
    }
    if (choice === league.my_team_id) {
      setMessage(null);
      onSaved(league);
      return;
    }
    setPending(true);
    const r = await api.leagues.setMyTeam(league.id, choice);
    setPending(false);
    if (r.kind === "ok") {
      setMessage({ kind: "ok", text: "Saved." });
      onSaved(r.data);
    } else if (r.kind === "error") {
      setMessage({ kind: "error", text: `${describeError(r.code, r.status).title}. ${r.message}` });
    } else if (r.kind === "unreachable") {
      setMessage({ kind: "error", text: r.message });
    }
  }

  if (!teams.length) {
    return <p className="text-sm text-muted">This league has no teams yet.</p>;
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3" aria-label="Choose my team">
      <fieldset>
        <legend className="mb-2 font-medium">Which team is yours?</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {teams.map((t) => (
            <label
              key={t.id}
              className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${
                choice === t.id ? "border-accent bg-accent-soft" : "border-line bg-surface"
              }`}
            >
              <input type="radio" name={name} className="mt-1" value={t.id} checked={choice === t.id} onChange={() => setChoice(t.id)} />
              <span className="min-w-0">
                <span className="block font-medium">{t.team_name}</span>
                <span className="block text-sm text-muted">
                  {t.owner_name ? `${t.owner_name} · ` : ""}
                  {t.players.length} players
                </span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn btn-primary" disabled={pending}>
          {pending ? "Saving…" : submitLabel}
        </button>
        <p role="status" className={`text-sm ${message?.kind === "error" ? "font-medium text-danger-ink" : "text-muted"}`}>
          {message?.text}
        </p>
      </div>
    </form>
  );
}
