import type { Metadata } from "next";
import { RankingsView } from "@/components/projections/RankingsView";
import { PageHeader } from "@/components/shell/PageHeader";

export const metadata: Metadata = { title: "Rest-of-season rankings" };

export default function RankingsPage() {
  return (
    <>
      <PageHeader
        title="Rest-of-season rankings"
        lead="Players ranked by simulated points over replacement for the rest of the regular season, under your league's scoring."
      />
      <RankingsView />
    </>
  );
}
