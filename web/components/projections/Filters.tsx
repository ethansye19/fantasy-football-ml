"use client";

import { useEffect, useId, useState, type ReactNode } from "react";
import { POSITIONS, type Position } from "@/lib/api/types";

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** One row of filters above the results (wraps on small screens). */
export function FilterBar({ children }: { children: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end gap-3" role="search" aria-label="Filters">
      {children}
    </div>
  );
}

export function PositionFilter({ value, onChange }: { value: Position | null; onChange: (p: Position | null) => void }) {
  const options: (Position | null)[] = [null, ...POSITIONS];
  return (
    <div role="group" aria-label="Position" className="flex flex-wrap gap-1">
      {options.map((p) => {
        const active = p === value;
        return (
          <button
            key={p ?? "all"}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(p)}
            className={`min-h-9 rounded-md border px-2.5 text-sm font-semibold ${
              active ? "border-accent bg-accent text-on-accent" : "border-line-strong bg-surface text-ink hover:bg-surface-2"
            }`}
          >
            {p ?? "All"}
          </button>
        );
      })}
    </div>
  );
}

export function SelectFilter<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">
        {label}
      </label>
      <select id={id} className="field min-h-9 py-1 text-sm" value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function SearchFilter({ value, onChange, label = "Search players" }: { value: string; onChange: (v: string) => void; label?: string }) {
  const id = useId();
  return (
    <div className="min-w-0 flex-1 basis-40">
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">
        {label}
      </label>
      <input
        id={id}
        type="search"
        className="field min-h-9 py-1 text-sm"
        value={value}
        maxLength={60}
        autoComplete="off"
        placeholder="Name"
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
