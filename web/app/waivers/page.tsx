import type { Metadata } from "next";
import { WaiversView } from "@/components/projections/WaiversView";
import { PageHeader } from "@/components/shell/PageHeader";

export const metadata: Metadata = { title: "Waivers" };

export default function WaiversPage() {
  return (
    <>
      <PageHeader
        title="Waiver wire"
        lead="Free agents ranked by how much they add to your best weekly lineups, each paired with the player you would drop."
      />
      <WaiversView />
    </>
  );
}
