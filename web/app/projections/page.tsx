import type { Metadata } from "next";
import { ProjectionsView } from "@/components/projections/ProjectionsView";
import { PageHeader } from "@/components/shell/PageHeader";

export const metadata: Metadata = { title: "Projections" };

export default function ProjectionsPage() {
  return (
    <>
      <PageHeader
        title="Weekly projections"
        lead="Each bar spans the 10th to the 90th percentile of simulated points when the player plays (floor to ceiling); the mark is the median. Boom and bust are the chances of a top finish or a poor one at the position."
      />
      <ProjectionsView />
    </>
  );
}
