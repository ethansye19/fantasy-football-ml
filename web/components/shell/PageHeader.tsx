import type { ReactNode } from "react";

export function PageHeader({ title, lead, children }: { title: string; lead?: ReactNode; children?: ReactNode }) {
  return (
    <div className="mb-6 space-y-2">
      <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{title}</h1>
      {lead ? <p className="max-w-3xl text-muted">{lead}</p> : null}
      {children}
    </div>
  );
}
