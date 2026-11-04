"use client";

import Link from "next/link";
import { useId, useRef, useState, type FormEvent } from "react";
import { ErrorSummary, TextField } from "@/components/forms/Field";
import { api } from "@/lib/api/client";
import { describeError, type ApiResult } from "@/lib/api/result";
import type { League, SleeperLeagueSummary, SleeperUserLeagues } from "@/lib/api/types";
import { MyTeamPicker } from "./MyTeamPicker";

type Step = "user" | "league" | "team" | "done";

const USERNAME = /^[A-Za-z0-9_]{1,40}$/;
const LEAGUE_ID = /^[0-9]{1,24}$/;

function failure(result: ApiResult<unknown>): string | null {
  if (result.kind === "ok") return null;
  if (result.kind === "error") return `${describeError(result.code, result.status).title}. ${result.message}`;
  if (result.kind === "unreachable") return result.message;
  return "Something went wrong. Try again.";
}

export function SleeperImport({ defaultSeason, onImported }: { defaultSeason: number; onImported: (league: League) => void }) {
  const [step, setStep] = useState<Step>("user");
  const [byId, setById] = useState(false);
  const [username, setUsername] = useState("");
  const [leagueIdInput, setLeagueIdInput] = useState("");
  const [season, setSeason] = useState(String(defaultSeason));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [listing, setListing] = useState<SleeperUserLeagues | null>(null);
  const [choice, setChoice] = useState<string | null>(null);
  const [league, setLeague] = useState<League | null>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const base = useId();
  const ids = { username: `${base}-username`, leagueId: `${base}-league-id`, season: `${base}-season` };

  const focusSummary = () => requestAnimationFrame(() => summaryRef.current?.focus());

  async function findLeagues(e: FormEvent) {
    e.preventDefault();
    const found: Record<string, string> = {};
    const seasonNum = Number(season);
    if (!/^\d{4}$/.test(season) || seasonNum < 2017 || seasonNum > 2100) found.season = "Enter a season between 2017 and 2100.";
    if (byId) {
      if (!LEAGUE_ID.test(leagueIdInput.trim())) found.leagueId = "A Sleeper league id is a long number, like 1389750342958223360.";
    } else if (!USERNAME.test(username.trim())) {
      found.username = "Use letters, numbers and underscores only.";
    }
    setErrors(found);
    setProblem(null);
    if (Object.keys(found).length) return focusSummary();

    setPending(true);
    try {
      if (byId) {
        await importLeague({ league_id: leagueIdInput.trim(), season: seasonNum });
        return;
      }
      const r = await api.sleeper.userLeagues(username.trim(), seasonNum);
      if (r.kind !== "ok") {
        setProblem(failure(r));
        return focusSummary();
      }
      setListing(r.data);
      setChoice(r.data.leagues.length === 1 ? (r.data.leagues[0]?.league_id ?? null) : null);
      setStep("league");
    } finally {
      setPending(false);
    }
  }

  async function importLeague(body: { league_id?: string; username?: string; season?: number }) {
    const r = await api.leagues.importSleeper(body);
    if (r.kind === "ok") {
      setLeague(r.data);
      setStep("team");
      return;
    }
    if (r.kind === "error" && r.code === "multiple_leagues") {
      const leagues = (r.details as { leagues?: SleeperLeagueSummary[] } | null)?.leagues ?? [];
      setListing({ user: { user_id: "", username: body.username ?? "", display_name: body.username ?? "" }, season: body.season ?? defaultSeason, leagues });
      setStep("league");
      return;
    }
    setProblem(failure(r));
    focusSummary();
  }

  async function onPickLeague(e: FormEvent) {
    e.preventDefault();
    setProblem(null);
    if (!choice) {
      setErrors({ league: "Choose a league to import." });
      return focusSummary();
    }
    setErrors({});
    setPending(true);
    try {
      await importLeague({ league_id: choice, username: listing?.user.username || username.trim(), season: Number(season) });
    } finally {
      setPending(false);
    }
  }

  const summaryItems = Object.entries(errors).map(([k, msg]) => ({
    href: `#${k === "league" ? `${base}-leagues` : ids[k as keyof typeof ids]}`,
    text: msg,
  }));

  return (
    <div className="space-y-5" data-testid="sleeper-import">
      <ol className="flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-label="Import steps">
        {[
          ["user", "1. Find your leagues"],
          ["league", "2. Pick a league"],
          ["team", "3. Choose your team"],
        ].map(([key, label]) => {
          const current = step === key || (step === "done" && key === "team");
          return (
            <li key={key} aria-current={current ? "step" : undefined} className={current ? "font-semibold text-ink" : "text-muted"}>
              {label}
            </li>
          );
        })}
      </ol>

      <ErrorSummary ref={summaryRef} items={summaryItems} formError={problem} title="The import did not work" />

      {step === "user" ? (
        <form noValidate onSubmit={findLeagues} className="space-y-4" aria-label="Find Sleeper leagues">
          <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
            {byId ? (
              <TextField
                id={ids.leagueId}
                label="Sleeper league id"
                hint="Found in the league's URL on Sleeper."
                inputMode="numeric"
                autoComplete="off"
                value={leagueIdInput}
                error={errors.leagueId}
                onChange={(e) => setLeagueIdInput(e.target.value)}
              />
            ) : (
              <TextField
                id={ids.username}
                label="Sleeper username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={username}
                error={errors.username}
                onChange={(e) => setUsername(e.target.value)}
              />
            )}
            <TextField
              id={ids.season}
              label="Season"
              inputMode="numeric"
              value={season}
              error={errors.season}
              onChange={(e) => setSeason(e.target.value)}
            />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" className="btn btn-primary" disabled={pending}>
              {pending ? "Looking…" : byId ? "Import league" : "Find my leagues"}
            </button>
            <button
              type="button"
              className="link text-sm"
              onClick={() => {
                setById((v) => !v);
                setErrors({});
                setProblem(null);
              }}
            >
              {byId ? "Use a username instead" : "I have a league id"}
            </button>
          </div>
          <p className="text-xs text-muted">Read only: nothing is changed on Sleeper.</p>
        </form>
      ) : null}

      {step === "league" && listing ? (
        <form noValidate onSubmit={onPickLeague} className="space-y-4" aria-label="Pick a Sleeper league">
          {listing.leagues.length === 0 ? (
            <div className="rounded-lg border border-line bg-surface-2 p-4" data-testid="no-sleeper-leagues">
              <p className="font-medium">
                No {listing.season} leagues for {listing.user.display_name || listing.user.username}.
              </p>
              <p className="text-sm text-muted">Check the season, or import by league id.</p>
            </div>
          ) : (
            <fieldset id={`${base}-leagues`} tabIndex={-1}>
              <legend className="mb-2 font-medium">
                Leagues for {listing.user.display_name || listing.user.username} in {listing.season}
              </legend>
              <div className="grid gap-2">
                {listing.leagues.map((l) => (
                  <label
                    key={l.league_id}
                    className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${
                      choice === l.league_id ? "border-accent bg-accent-soft" : "border-line bg-surface"
                    }`}
                  >
                    <input
                      type="radio"
                      name={`${base}-league`}
                      className="mt-1"
                      value={l.league_id}
                      checked={choice === l.league_id}
                      onChange={() => setChoice(l.league_id)}
                    />
                    <span>
                      <span className="block font-medium">{l.name}</span>
                      <span className="block text-sm text-muted">
                        {l.num_teams} teams · {l.preset_like ? l.preset_like.toUpperCase() : "custom scoring"} · {l.status.replace(/_/g, " ")}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
              {errors.league ? <p className="mt-2 text-sm font-medium text-danger-ink">{errors.league}</p> : null}
            </fieldset>
          )}
          <div className="flex flex-wrap gap-3">
            {listing.leagues.length ? (
              <button type="submit" className="btn btn-primary" disabled={pending}>
                {pending ? "Importing…" : "Import league"}
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                setStep("user");
                setProblem(null);
                setErrors({});
              }}
            >
              Back
            </button>
          </div>
        </form>
      ) : null}

      {step === "team" && league ? (
        <div className="space-y-3">
          <p>
            Imported <strong>{league.name}</strong> ({league.num_teams} teams).
            {league.scoring.unsupported_keys.length ? (
              <span className="block text-sm text-muted">
                Ignored scoring keys: <span className="font-mono text-xs">{league.scoring.unsupported_keys.join(", ")}</span>
              </span>
            ) : null}
          </p>
          <MyTeamPicker
            league={league}
            submitLabel="Use this team"
            onSaved={(updated) => {
              setLeague(updated);
              setStep("done");
              onImported(updated);
            }}
          />
        </div>
      ) : null}

      {step === "done" && league ? (
        <div className="rounded-lg border border-line bg-accent-soft p-4" role="status" data-testid="import-done">
          <p className="font-semibold">{league.name} is ready.</p>
          <p className="mt-1 text-sm">
            It is now the league this browser uses.{" "}
            <Link href="/projections" className="link">
              Go to projections
            </Link>
          </p>
        </div>
      ) : null}
    </div>
  );
}
