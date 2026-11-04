"use client";

/**
 * The league the person is working with. Only its id is kept in this
 * browser (localStorage, a per-browser convenience); the league itself,
 * including "my team", lives in the API.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { api } from "./api/client";
import { useApi } from "./api/hooks";
import type { ApiResult } from "./api/result";
import type { League, LeagueTeam } from "./api/types";

const STORAGE_KEY = "ffml.leagueId";
const CHANGE_EVENT = "ffml:league-change";

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStored(id: string | null): void {
  memoryId = id;
  try {
    if (id) window.localStorage.setItem(STORAGE_KEY, id);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage blocked: the choice lasts for this page view only
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

let memoryId: string | null = null;

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

function getSnapshot(): string | null {
  return readStored() ?? memoryId;
}

function getServerSnapshot(): string | null {
  return null;
}

interface LeagueValue {
  /** True once the browser value has been read (false during server render). */
  hydrated: boolean;
  leagueId: string | null;
  league: League | null;
  myTeam: LeagueTeam | null;
  result: ApiResult<League> | null;
  loading: boolean;
  setLeagueId: (id: string | null) => void;
  /** Store a league object returned by a mutation and re-read it. */
  reload: () => void;
}

const LeagueContext = createContext<LeagueValue | null>(null);

export function LeagueProvider({ children }: { children: ReactNode }) {
  const leagueId = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const hydrated = useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
  const { result, loading, reload } = useApi<League>(leagueId ? `league:${leagueId}` : null, (signal) =>
    api.leagues.get(leagueId as string, { signal }),
  );

  // A stored id for a league that no longer exists is forgotten.
  useEffect(() => {
    if (result?.kind === "error" && result.code === "league_not_found") writeStored(null);
  }, [result]);

  const setLeagueId = useCallback((id: string | null) => writeStored(id), []);

  const value = useMemo<LeagueValue>(() => {
    const league = result?.kind === "ok" ? result.data : null;
    const myTeam = league?.teams?.find((t) => t.id === league.my_team_id) ?? league?.teams?.find((t) => t.is_me) ?? null;
    return { hydrated, leagueId, league, myTeam, result, loading, setLeagueId, reload };
  }, [hydrated, leagueId, result, loading, setLeagueId, reload]);

  return <LeagueContext.Provider value={value}>{children}</LeagueContext.Provider>;
}

export function useLeague(): LeagueValue {
  const ctx = useContext(LeagueContext);
  if (!ctx) throw new Error("useLeague must be used inside <LeagueProvider>");
  return ctx;
}
