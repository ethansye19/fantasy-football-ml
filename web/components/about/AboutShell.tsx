"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { ABOUT_ITEMS } from "@/lib/nav";
import { CONTENT_AS_OF } from "@/lib/content";
import type { ContentSource } from "./useMetaContent";

export function AboutNav() {
  const pathname = usePathname() ?? "";
  return (
    <nav aria-label="About pages" className="mb-6">
      <ul className="flex flex-wrap gap-x-1 border-b border-line">
        {ABOUT_ITEMS.map((item) => {
          const active = pathname === item.href;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`-mb-px inline-block border-b-2 px-3 py-2 text-sm font-medium ${
                  active ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink"
                }`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** Says where the page's content came from; offers a retry when it is built-in copy. */
export function SourceNote({ source, reason, onRetry }: { source: ContentSource; reason: string | null; onRetry: () => void }) {
  if (source !== "fallback") return null;
  return (
    <p className="mb-6 rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-ink" data-testid="fallback-note" role="status">
      Showing the built-in copy from {CONTENT_AS_OF}. {reason}{" "}
      <button type="button" className="link" onClick={onRetry}>
        Load from the API
      </button>
    </p>
  );
}

export function Section({ title, id, children }: { title: string; id: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="space-y-3">
      <h2 id={id} className="text-xl font-semibold tracking-tight">
        {title}
      </h2>
      {children}
    </section>
  );
}
