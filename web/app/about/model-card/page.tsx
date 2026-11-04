import type { Metadata } from "next";
import { AboutNav } from "@/components/about/AboutShell";
import { ModelCardView } from "@/components/about/ModelCardView";
import { PageHeader } from "@/components/shell/PageHeader";

export const metadata: Metadata = { title: "Model card" };

export default function ModelCardPage() {
  return (
    <>
      <PageHeader title="Model card" lead="The model behind the projections: its status, method, definitions and limits." />
      <AboutNav />
      <ModelCardView />
    </>
  );
}
