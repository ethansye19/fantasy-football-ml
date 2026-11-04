"use client";

import { useSyncExternalStore } from "react";

/** "system" means: follow prefers-color-scheme (no data-theme attribute). */
export type ThemeChoice = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "ffml.theme";
const CHANGE_EVENT = "ffml:theme-change";

/**
 * Runs before first paint (inlined in <head>) so the page never flashes the
 * wrong theme. Kept tiny and dependency-free.
 */
export const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_STORAGE_KEY}");if(t==="light"||t==="dark"){document.documentElement.setAttribute("data-theme",t)}}catch(e){}})();`;

function readChoice(): ThemeChoice {
  const attr = document.documentElement.getAttribute("data-theme");
  return attr === "light" || attr === "dark" ? attr : "system";
}

function systemPrefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function setThemeChoice(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
  try {
    if (choice === "system") window.localStorage.removeItem(THEME_STORAGE_KEY);
    else window.localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    // storage blocked: the choice lasts for this page view
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, onChange);
  const mq = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  mq?.addEventListener?.("change", onChange);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    mq?.removeEventListener?.("change", onChange);
  };
}

/** `${choice}:${effective}` so one snapshot string carries both values. */
function snapshot(): string {
  const choice = readChoice();
  const effective = choice === "system" ? (systemPrefersDark() ? "dark" : "light") : choice;
  return `${choice}:${effective}`;
}

export function useTheme(): { choice: ThemeChoice; effective: "light" | "dark"; hydrated: boolean } {
  const value = useSyncExternalStore(subscribe, snapshot, () => "server:light");
  const [choice, effective] = value.split(":") as [string, "light" | "dark"];
  return {
    choice: choice === "server" ? "system" : (choice as ThemeChoice),
    effective,
    hydrated: choice !== "server",
  };
}
