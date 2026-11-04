import type { Metadata } from "next";
import { AboutNav } from "@/components/about/AboutShell";
import { AboutOverview } from "@/components/about/AboutOverview";
import { PageHeader } from "@/components/shell/PageHeader";

export const metadata: Metadata = { title: "About" };

export default function AboutPage() {
  return (
    <>
      <PageHeader title="About" lead="How the projections are made, what they mean, and where the data comes from." />
      <AboutNav />
      <AboutOverview />
    </>
  );
}
