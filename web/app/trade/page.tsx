import type { Metadata } from "next";
import { TradeView } from "@/components/projections/TradeView";
import { PageHeader } from "@/components/shell/PageHeader";

export const metadata: Metadata = { title: "Trade analyzer" };

export default function TradePage() {
  return (
    <>
      <PageHeader
        title="Trade analyzer"
        lead="Rest-of-season value over replacement for each side, how your best weekly lineups change, and how even the deal is."
      />
      <TradeView />
    </>
  );
}
