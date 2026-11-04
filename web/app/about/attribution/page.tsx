import type { Metadata } from "next";
import { AboutNav } from "@/components/about/AboutShell";
import { AttributionView } from "@/components/about/AttributionView";
import { PageHeader } from "@/components/shell/PageHeader";

export const metadata: Metadata = { title: "Data and attribution" };

export default function AttributionPage() {
  return (
    <>
      <PageHeader title="Data and attribution" lead="Every data source the app uses, with its license and the credit it asks for." />
      <AboutNav />
      <AttributionView />
    </>
  );
}
