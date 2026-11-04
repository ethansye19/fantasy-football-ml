import type { Metadata } from "next";
import { PageHeader } from "@/components/shell/PageHeader";
import { SetupView } from "@/components/setup/SetupView";

export const metadata: Metadata = { title: "League setup" };

export default function SetupPage() {
  return (
    <>
      <PageHeader
        title="League setup"
        lead="Import a Sleeper league or enter your scoring and roster rules by hand. Every view uses this league's rules."
      />
      <SetupView />
    </>
  );
}
