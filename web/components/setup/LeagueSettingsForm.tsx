"use client";

import { useMemo, useRef, useState, type FormEvent } from "react";
import { ErrorSummary, TextField, fieldId } from "@/components/forms/Field";
import { api } from "@/lib/api/client";
import type { League, PresetName } from "@/lib/api/types";
import {
  type FieldErrors,
  type FormMode,
  type LeagueFormValues,
  initialCreateValues,
  initialEditValues,
  presetScoring,
  serverErrors,
  toManualBody,
  toSettingsBody,
  validateLeagueForm,
} from "@/lib/league-form";
import { IDP_SLOTS, MAX_PER_SLOT, PRESET_LABELS, SCORING_FIELDS, SLOT_INFO } from "@/lib/scoring";
import { PlayerPicker } from "./PlayerPicker";

const GROUPS = ["Passing", "Rushing", "Receiving", "Other", "Kicking", "Defense"] as const;

function labelsFor(values: LeagueFormValues): Record<string, string> {
  const out: Record<string, string> = {
    name: "League name",
    season: "Season",
    num_teams: "Number of teams",
    playoff_week_start: "Playoffs start in week",
    roster: "Roster slots",
    scoring: "Scoring",
    team_name: "Your team name",
    players: "Players on your team",
  };
  for (const f of SCORING_FIELDS) out[`scoring.${f.key}`] = f.label;
  for (const k of Object.keys(values.scoring)) out[`scoring.${k}`] ??= k;
  return out;
}

export function LeagueSettingsForm({
  mode,
  league,
  defaultSeason,
  onSaved,
}: {
  mode: FormMode;
  league?: League;
  defaultSeason: number;
  onSaved: (league: League) => void;
}) {
  const [values, setValues] = useState<LeagueFormValues>(() =>
    mode === "edit" && league ? initialEditValues(league) : initialCreateValues(defaultSeason),
  );
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const labels = useMemo(() => labelsFor(values), [values]);
  const prefix = mode === "edit" ? "edit" : "new";
  const id = (key: string) => `${prefix}-${fieldId(key)}`;

  const set = <K extends keyof LeagueFormValues>(key: K, value: LeagueFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }));
    setSaved(null);
  };

  const extraKeys = Object.keys(values.scoring).filter((k) => !SCORING_FIELDS.some((f) => f.key === k));

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSaved(null);
    const found = validateLeagueForm(values, mode);
    setErrors(found);
    setFormError(null);
    if (Object.keys(found).length) {
      requestAnimationFrame(() => summaryRef.current?.focus());
      return;
    }
    let result;
    setPending(true);
    try {
      if (mode === "create") {
        result = await api.leagues.createManual(toManualBody(values));
      } else {
        const body = toSettingsBody(values, league as League);
        if (!Object.keys(body).length) {
          setSaved("Nothing changed.");
          return;
        }
        result = await api.leagues.updateSettings((league as League).id, body);
      }
    } finally {
      setPending(false);
    }
    if (result.kind === "ok") {
      setSaved(mode === "create" ? "League created." : "Settings saved.");
      if (mode === "edit") setValues(initialEditValues(result.data));
      onSaved(result.data);
      return;
    }
    if (result.kind === "error") {
      const mapped = serverErrors(result);
      setErrors(mapped.fields);
      setFormError(mapped.form);
    } else if (result.kind === "unreachable") {
      setFormError(result.message);
    } else {
      setFormError("The settings were not saved. Try again.");
    }
    requestAnimationFrame(() => summaryRef.current?.focus());
  }

  const summaryItems = Object.entries(errors).map(([k, msg]) => ({ href: `#${id(k)}`, text: `${labels[k] ?? k}: ${msg}` }));

  return (
    <form noValidate onSubmit={onSubmit} className="space-y-8" aria-label={mode === "create" ? "Manual league settings" : "League settings"}>
      <ErrorSummary ref={summaryRef} items={summaryItems} formError={formError} title="The settings were not saved" />

      <fieldset className="space-y-4">
        <legend className="mb-2 text-lg font-semibold">League</legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            id={id("name")}
            label="League name"
            value={values.name}
            maxLength={100}
            autoComplete="off"
            required
            error={errors.name}
            onChange={(e) => set("name", e.target.value)}
          />
          {mode === "create" ? (
            <>
              <TextField
                id={id("season")}
                label="Season"
                inputMode="numeric"
                value={values.season}
                required
                error={errors.season}
                onChange={(e) => set("season", e.target.value)}
              />
              <TextField
                id={id("num_teams")}
                label="Number of teams"
                inputMode="numeric"
                value={values.numTeams}
                required
                error={errors.num_teams}
                onChange={(e) => set("numTeams", e.target.value)}
              />
            </>
          ) : null}
          <TextField
            id={id("playoff_week_start")}
            label="Playoffs start in week"
            hint="Optional. Week 1 to 18."
            inputMode="numeric"
            value={values.playoffWeekStart}
            error={errors.playoff_week_start}
            onChange={(e) => set("playoffWeekStart", e.target.value)}
          />
        </div>
      </fieldset>

      <fieldset className="space-y-4">
        <legend className="mb-2 text-lg font-semibold">Scoring</legend>
        {errors.scoring ? (
          <p id={id("scoring")} className="text-sm font-medium text-danger-ink">
            {errors.scoring}
          </p>
        ) : null}
        <fieldset>
          <legend className="mb-2 text-sm font-medium">Start from a preset</legend>
          <div className="flex flex-wrap gap-2">
            {(Object.keys(PRESET_LABELS) as PresetName[]).map((p) => (
              <label
                key={p}
                className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                  values.preset === p ? "border-accent bg-accent-soft" : "border-line-strong bg-surface"
                }`}
              >
                <input
                  type="radio"
                  name={`${prefix}-preset`}
                  value={p}
                  checked={values.preset === p}
                  onChange={() => {
                    setValues((v) => ({ ...v, preset: p, scoring: { ...presetScoring(p) } }));
                    setSaved(null);
                  }}
                />
                {PRESET_LABELS[p]}
              </label>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted">
            Picking a preset resets the values below. {values.preset === "custom" ? "This league's scoring matches no preset." : ""}
          </p>
        </fieldset>

        <ScoringGroups values={values} errors={errors} id={id} advanced={false} onChange={(k, v) => set("scoring", { ...values.scoring, [k]: v })} />
        <details className="rounded-lg border border-line bg-surface p-3">
          <summary className="cursor-pointer font-medium">More scoring options</summary>
          <div className="mt-4">
            <ScoringGroups values={values} errors={errors} id={id} advanced onChange={(k, v) => set("scoring", { ...values.scoring, [k]: v })} />
          </div>
        </details>

        {extraKeys.length ? (
          <div className="space-y-2">
            <h3 className="text-sm font-semibold">Other keys from this league</h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {extraKeys.map((k) => (
                <TextField
                  key={k}
                  id={id(`scoring.${k}`)}
                  label={<span className="font-mono text-xs">{k}</span>}
                  inputMode="decimal"
                  value={values.scoring[k] ?? ""}
                  error={errors[`scoring.${k}`]}
                  onChange={(e) => set("scoring", { ...values.scoring, [k]: e.target.value })}
                />
              ))}
            </div>
          </div>
        ) : null}
        {league?.scoring.unsupported_keys?.length ? (
          <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-sm">
            Ignored by the scoring engine: <span className="font-mono text-xs">{league.scoring.unsupported_keys.join(", ")}</span>
          </p>
        ) : null}
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="mb-2 text-lg font-semibold">Roster slots</legend>
        {errors.roster ? (
          <p id={id("roster")} className="text-sm font-medium text-danger-ink" tabIndex={-1}>
            {errors.roster}
          </p>
        ) : null}
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {SLOT_INFO.map((s) => (
            <SlotCounter
              key={s.slot}
              id={id(`slot-${s.slot}`)}
              label={s.label}
              hint={s.hint}
              value={values.slots[s.slot] ?? 0}
              onChange={(n) => set("slots", { ...values.slots, [s.slot]: n })}
            />
          ))}
        </div>
        {IDP_SLOTS.some((s) => values.slots[s]) ? (
          <p className="text-sm text-muted">
            Defensive player slots ({IDP_SLOTS.filter((s) => values.slots[s]).join(", ")}) are kept but ignored.
          </p>
        ) : null}
      </fieldset>

      {mode === "create" ? (
        <fieldset className="space-y-4">
          <legend className="mb-2 text-lg font-semibold">Your team</legend>
          <TextField
            id={id("team_name")}
            label="Your team name"
            value={values.teamName}
            maxLength={100}
            error={errors.team_name}
            onChange={(e) => set("teamName", e.target.value)}
          />
          <PlayerPicker id={id("players")} value={values.players} error={errors.players} onChange={(players) => set("players", players)} />
        </fieldset>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn btn-primary" disabled={pending}>
          {pending ? "Saving…" : mode === "create" ? "Create league" : "Save settings"}
        </button>
        <p role="status" className="text-sm text-muted" data-testid="form-status">
          {saved}
        </p>
      </div>
    </form>
  );
}

function ScoringGroups({
  values,
  errors,
  id,
  advanced,
  onChange,
}: {
  values: LeagueFormValues;
  errors: FieldErrors;
  id: (key: string) => string;
  advanced: boolean;
  onChange: (key: string, value: string) => void;
}) {
  return (
    <div className="space-y-4">
      {GROUPS.map((group) => {
        const fields = SCORING_FIELDS.filter((f) => f.group === group && Boolean(f.advanced) === advanced);
        if (!fields.length) return null;
        return (
          <div key={group}>
            <h3 className="mb-2 text-sm font-semibold text-muted">{group}</h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {fields.map((f) => (
                <TextField
                  key={f.key}
                  id={id(`scoring.${f.key}`)}
                  label={f.label}
                  inputMode="decimal"
                  autoComplete="off"
                  value={values.scoring[f.key] ?? "0"}
                  error={errors[`scoring.${f.key}`]}
                  onChange={(e) => onChange(f.key, e.target.value)}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SlotCounter({
  id,
  label,
  hint,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  value: number;
  onChange: (n: number) => void;
}) {
  const clamp = (n: number) => Math.max(0, Math.min(MAX_PER_SLOT, Number.isFinite(n) ? Math.round(n) : 0));
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-line bg-surface px-3 py-2">
      <label htmlFor={id} className="min-w-0 text-sm">
        <span className="block font-medium">{label}</span>
        {hint ? <span className="block text-xs text-muted">{hint}</span> : null}
      </label>
      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          className="btn btn-secondary h-9 min-h-0 w-9 p-0"
          aria-label={`One fewer ${label} slot`}
          onClick={() => onChange(clamp(value - 1))}
          disabled={value <= 0}
        >
          −
        </button>
        <input
          id={id}
          type="number"
          min={0}
          max={MAX_PER_SLOT}
          inputMode="numeric"
          className="field num h-9 min-h-0 w-14 px-1 text-center"
          value={value}
          onChange={(e) => onChange(clamp(Number(e.target.value)))}
        />
        <button
          type="button"
          className="btn btn-secondary h-9 min-h-0 w-9 p-0"
          aria-label={`One more ${label} slot`}
          onClick={() => onChange(clamp(value + 1))}
          disabled={value >= MAX_PER_SLOT}
        >
          +
        </button>
      </div>
    </div>
  );
}
