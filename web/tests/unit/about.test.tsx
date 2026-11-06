import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AttributionView } from "@/components/about/AttributionView";
import { LimitationsView } from "@/components/about/LimitationsView";
import { ModelCardView, registryStatusLabel } from "@/components/about/ModelCardView";
import { ATTRIBUTION, MODEL_CARD } from "@/mock/fixtures.mjs";
import { claimsApproval, stubApi, visibleText } from "./helpers";

const PROD_CARD = {
  ...MODEL_CARD,
  environment: "production",
  approval_status: "not approved",
  label: "not approved",
  serving: false,
  models: [
    { name: "chain_v1", version: "v1", registry_status: "rejected", serving: false, created_at: "2026-11-02T00:00:00Z" },
    { name: "chain_v2", version: "v2", registry_status: "awaiting_release", serving: false, created_at: "2026-11-05T00:00:00Z" },
  ],
};

describe("ModelCardView", () => {
  it("renders the API card with a not-approved label and no approval claim", async () => {
    stubApi({ "/api/meta/model-card": { status: 200, body: PROD_CARD } });
    render(<ModelCardView />);
    const status = await screen.findByTestId("model-status");
    expect(screen.getByTestId("not-approved-label")).toHaveTextContent(/not approved/i);
    expect(status).toHaveTextContent(MODEL_CARD.status_summary);
    expect(screen.getByText("Rejected: did not pass its test")).toBeInTheDocument();
    expect(screen.getByText("Awaiting release")).toBeInTheDocument();
    expect(claimsApproval(visibleText())).toBe(false);
    expect(screen.queryByTestId("fallback-note")).toBeNull();
  });

  it("shows the private-test notice", async () => {
    stubApi({
      "/api/meta/model-card": {
        status: 200,
        body: { ...PROD_CARD, environment: "private_test", models: [], notice: "Private test environment: labeled not approved." },
        headers: { "X-FFML-Approval": "not-approved" },
      },
    });
    render(<ModelCardView />);
    expect(await screen.findByText("Private test environment: labeled not approved.")).toBeInTheDocument();
  });

  it("falls back to built-in copy when the endpoint is missing", async () => {
    stubApi({});
    render(<ModelCardView />);
    expect(await screen.findByTestId("fallback-note")).toHaveTextContent("does not provide this page yet");
    expect(screen.getByTestId("model-status")).toHaveTextContent("No model is approved.");
    expect(screen.getByTestId("not-approved-label")).toBeInTheDocument();
  });

  it("falls back when the API is unreachable", async () => {
    stubApi({
      "/api/meta/model-card": { status: 502, body: { error: { code: "api_unreachable", message: "down", details: null } } },
    });
    render(<ModelCardView />);
    expect(await screen.findByTestId("fallback-note")).toHaveTextContent("could not be reached");
  });
});

describe("registryStatusLabel", () => {
  it("maps the v1.3 words and never claims approval on its own", () => {
    expect(registryStatusLabel("candidate")).toMatch(/under test/);
    expect(registryStatusLabel("awaiting_release")).toBe("Awaiting release");
    expect(registryStatusLabel("live", true)).toBe("Live, serving projections");
    expect(registryStatusLabel("live", false)).toBe("Live");
    for (const word of ["candidate", "rejected", "retired", "awaiting_release", "live", "approved", "odd_word", null]) {
      expect(claimsApproval(registryStatusLabel(word, false))).toBe(false);
      expect(claimsApproval(registryStatusLabel(word, true))).toBe(false);
    }
  });
});

describe("AttributionView", () => {
  it("renders every source with its attribution text, FTN included", async () => {
    stubApi({ "/api/meta/attribution": { status: 200, body: ATTRIBUTION } });
    render(<AttributionView />);
    const nflverse = await screen.findByTestId("source-nflverse");
    expect(nflverse).toHaveTextContent("Data from nflverse (github.com/nflverse/nflverse-data), CC-BY-4.0.");
    expect(screen.getByTestId("source-ftn")).toHaveTextContent("FTN charting data: ftndata.com via nflverse, CC-BY-SA-4.0.");
    expect(screen.getByTestId("source-ftn")).toHaveTextContent("Not in use");
    expect(screen.getByTestId("source-sleeper")).toHaveTextContent("League and player data from Sleeper.");
  });

  it("uses DATA_SOURCES.md wording when the endpoint fails", async () => {
    stubApi({ "/api/meta/attribution": { status: 500, body: { error: { code: "internal_error", message: "boom", details: null } } } });
    render(<AttributionView />);
    expect(await screen.findByTestId("fallback-note")).toBeInTheDocument();
    expect(screen.getByTestId("source-nflverse")).toHaveTextContent("Modified: aggregated and modeled by this project.");
    expect(screen.getByTestId("source-nflverse")).toHaveTextContent("nflreadpy");
    expect(screen.getByTestId("source-ftn")).toHaveTextContent("ftndata.com via nflverse, CC-BY-SA-4.0");
  });

  it("falls back when the API lists no sources", async () => {
    stubApi({ "/api/meta/attribution": { status: 200, body: { sources: [] } } });
    render(<AttributionView />);
    expect(await screen.findByTestId("fallback-note")).toBeInTheDocument();
  });
});

describe("LimitationsView", () => {
  it("lists model limitations from the API, including the K/DST low-confidence note", async () => {
    stubApi({ "/api/meta/model-card": { status: 200, body: PROD_CARD } });
    render(<LimitationsView />);
    const list = await screen.findByTestId("model-limitations");
    expect(list).toHaveTextContent(/low confidence/);
    expect(screen.getByText("Few simulations for some players")).toBeInTheDocument();
  });
});
