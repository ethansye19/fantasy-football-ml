import type { Metadata } from "next";
import { StartSitView } from "@/components/projections/StartSitView";
import { PageHeader } from "@/components/shell/PageHeader";

export const metadata: Metadata = { title: "Start/sit" };

export default function StartSitPage() {
  return (
    <>
      <PageHeader
        title="Start/sit"
        lead="Compare two to four players. The odds come from paired simulations, so teammates and opponents keep their correlation."
      />
      <StartSitView />
    </>
  );
}
