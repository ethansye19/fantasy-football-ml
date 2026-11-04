import type { Metadata } from "next";
import { AboutNav } from "@/components/about/AboutShell";
import { LimitationsView } from "@/components/about/LimitationsView";
import { PageHeader } from "@/components/shell/PageHeader";

export const metadata: Metadata = { title: "Limitations" };

export default function LimitationsPage() {
  return (
    <>
      <PageHeader title="Limitations" lead="Known weaknesses of the model and of the app, listed plainly." />
      <AboutNav />
      <LimitationsView />
    </>
  );
}
