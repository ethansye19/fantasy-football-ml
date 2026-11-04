import { pct } from "@/lib/format";

/**
 * One horizontal bar per item, growing from a shared baseline: thin marks,
 * a rounded data end, the value printed at the tip in ink.
 */
export function ChanceBars({ items, label }: { items: { id: string; name: string; value: number }[]; label: string }) {
  const sorted = [...items].sort((a, b) => b.value - a.value);
  return (
    <figure className="space-y-2" data-testid="chance-bars">
      <figcaption className="text-sm font-semibold">{label}</figcaption>
      <ul className="space-y-2">
        {sorted.map((it) => (
          <li key={it.id} className="grid grid-cols-[minmax(6rem,10rem)_1fr] items-center gap-3 text-sm">
            <span className="truncate" title={it.name}>
              {it.name}
            </span>
            <span className="flex items-center gap-2" aria-label={`${it.name}: ${pct(it.value)}`}>
              <span className="relative h-4 flex-1" aria-hidden="true">
                <span className="absolute inset-y-0 left-0 w-px" style={{ background: "var(--border-strong)" }} />
                <span
                  className="absolute inset-y-0 left-0 rounded-r"
                  style={{ width: `${Math.max(0.5, it.value * 100)}%`, background: "var(--strip-band)" }}
                />
              </span>
              <span className="num w-10 shrink-0 text-right font-semibold">{pct(it.value)}</span>
            </span>
          </li>
        ))}
      </ul>
    </figure>
  );
}
