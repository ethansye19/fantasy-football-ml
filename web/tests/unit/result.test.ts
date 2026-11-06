import { describe, expect, it } from "vitest";
import {
  DEFAULT_NO_APPROVED_MODEL_MESSAGE,
  describeError,
  playerNotFoundIds,
  readApproval,
  toResult,
  validationDetails,
} from "@/lib/api/result";

const GATE_BODY = JSON.stringify({
  status: "no_approved_model",
  message: "No approved model. Projections are off until a model passes its pre-registered test.",
});

const ENVELOPE = {
  approval_status: "not approved",
  label: "not approved",
  environment: "private_test",
  run: {
    source: "replay",
    run_id: "2024w10-20261103T010442Z-replay",
    season: 2024,
    week: 10,
    model_version: "chain_v1",
    written_at: "2026-11-03T01:04:55Z",
    n_sims: 2000,
    is_replay: true,
    has_rest_of_season: true,
  },
  notices: [
    "Not approved: these numbers come from a model that has not passed its pre-registered test. Private test environment only; not a recommendation.",
    "Historical replay of 2024 week 10.",
  ],
  players: [],
};

describe("toResult: 503 gate states", () => {
  it("maps 503 no_approved_model to its own state, not an error", () => {
    const r = toResult(503, "none", GATE_BODY);
    expect(r.kind).toBe("no_approved_model");
    if (r.kind !== "no_approved_model") throw new Error();
    expect(r.message).toMatch(/^No approved model\./);
  });

  it("treats a 503 with X-FFML-Approval: none as no_approved_model even with an unreadable body", () => {
    const r = toResult(503, "none", "<html>oops</html>");
    expect(r.kind).toBe("no_approved_model");
    if (r.kind !== "no_approved_model") throw new Error();
    expect(r.message).toBe(DEFAULT_NO_APPROVED_MODEL_MESSAGE);
  });

  it("maps 503 no_private_test_snapshot with a not-approved label", () => {
    const body = JSON.stringify({
      status: "no_private_test_snapshot",
      message: "Private test environment: nothing is served.",
      approval_status: "not approved",
      label: "not approved",
    });
    const r = toResult(503, "not-approved", body);
    expect(r.kind).toBe("no_private_test_snapshot");
    if (r.kind !== "no_private_test_snapshot") throw new Error();
    expect(r.approval.approved).toBe(false);
    expect(r.approval.label).toBe("not approved");
    expect(r.message).toContain("nothing is served");
  });

  it("keeps an ordinary 503 without gate markers as an error", () => {
    const r = toResult(503, null, JSON.stringify({ error: { code: "unavailable", message: "Down", details: null } }));
    expect(r.kind).toBe("error");
  });
});

describe("toResult: the not-approved envelope", () => {
  it("returns ok data with a not-approved observation, its label, run and notices", () => {
    const r = toResult(200, "not-approved", JSON.stringify(ENVELOPE), { expectEnvelope: true });
    expect(r.kind).toBe("ok");
    if (r.kind !== "ok") throw new Error();
    expect(r.approval).not.toBeNull();
    expect(r.approval?.approved).toBe(false);
    expect(r.approval?.status).toBe("not approved");
    expect(r.approval?.label).toBe("not approved");
    expect(r.approval?.environment).toBe("private_test");
    expect(r.approval?.run?.run_id).toBe("2024w10-20261103T010442Z-replay");
    expect(r.approval?.notices).toHaveLength(2);
    expect(r.approval?.header).toBe("not-approved");
  });

  it("is approved only when the body says approved and the header agrees", () => {
    const body = JSON.stringify({ ...ENVELOPE, approval_status: "approved", label: "approved", environment: "production" });
    const both = toResult(200, "approved", body, { expectEnvelope: true });
    expect(both.kind === "ok" && both.approval?.approved).toBe(true);

    const headerSaysNo = toResult(200, "not-approved", body, { expectEnvelope: true });
    expect(headerSaysNo.kind === "ok" && headerSaysNo.approval?.approved).toBe(false);
    expect(headerSaysNo.kind === "ok" && headerSaysNo.approval?.label).toBe("not approved");

    const noneHeader = toResult(200, "none", body, { expectEnvelope: true });
    expect(noneHeader.kind === "ok" && noneHeader.approval?.approved).toBe(false);

    const strangeHeader = toResult(200, "yes please", body, { expectEnvelope: true });
    expect(strangeHeader.kind === "ok" && strangeHeader.approval?.approved).toBe(false);
  });

  it("reads a projection response without an envelope as not approved", () => {
    const r = toResult(200, null, JSON.stringify({ players: [] }), { expectEnvelope: true });
    expect(r.kind).toBe("ok");
    if (r.kind !== "ok") throw new Error();
    expect(r.approval?.approved).toBe(false);
    expect(r.approval?.label).toBe("not approved");
  });

  it("never shows the label 'approved' on a response that is not approved", () => {
    const o = readApproval({ approval_status: "not approved", label: "Approved" }, null);
    expect(o?.label).toBe("not approved");
  });

  it("returns no observation for routes without approval fields or header", () => {
    expect(readApproval({ leagues: [] }, null)).toBeNull();
    const r = toResult(200, null, JSON.stringify({ leagues: [] }));
    expect(r.kind === "ok" && r.approval).toBeNull();
  });

  it("observes the private-test header on non-projection routes", () => {
    const r = toResult(200, "not-approved", JSON.stringify({ leagues: [] }));
    expect(r.kind === "ok" && r.approval?.approved).toBe(false);
  });

  it("keeps the envelope of a 501 not_implemented skeleton response", () => {
    const body = JSON.stringify({ ...ENVELOPE, error: { code: "not_implemented", message: "Waivers is not implemented yet." } });
    const r = toResult(501, "not-approved", body, { expectEnvelope: true });
    expect(r.kind).toBe("error");
    if (r.kind !== "error") throw new Error();
    expect(r.code).toBe("not_implemented");
    expect(r.approval?.approved).toBe(false);
    expect(r.approval?.notices).toHaveLength(2);
  });

  it("does not read /meta/approval-like bodies as an envelope on non-projection routes", () => {
    const body = JSON.stringify({ environment: "production", serving: false, approval_status: "not approved", label: "not approved" });
    const r = toResult(200, null, body);
    expect(r.kind === "ok" && r.approval).toBeNull();
  });
});

describe("toResult: errors", () => {
  it("reads the error envelope", () => {
    const r = toResult(
      404,
      null,
      JSON.stringify({ error: { code: "league_not_found", message: "No league with id x", details: null } }),
    );
    expect(r).toMatchObject({ kind: "error", status: 404, code: "league_not_found", message: "No league with id x" });
  });

  it("exposes 422 validation details", () => {
    const details = [{ loc: ["body", "num_teams"], msg: "must be at least 2", type: "greater_than_equal" }];
    const r = toResult(422, null, JSON.stringify({ error: { code: "validation_error", message: "invalid", details } }));
    expect(validationDetails(r)).toEqual(details);
  });

  it("keeps multiple_leagues details", () => {
    const details = { leagues: [{ league_id: "1", name: "A" }] };
    const r = toResult(409, null, JSON.stringify({ error: { code: "multiple_leagues", message: "pick one", details } }));
    expect(r.kind === "error" && r.details).toEqual(details);
  });

  it("lists the unknown ids of a 422 player_not_found (v1.3)", () => {
    const r = toResult(422, null, JSON.stringify({ error: { code: "player_not_found", message: "Unknown player ids.", details: { player_ids: ["x1", "00-1"] } } }));
    expect(r).toMatchObject({ kind: "error", status: 422, code: "player_not_found" });
    expect(playerNotFoundIds(r)).toEqual(["x1", "00-1"]);
    expect(playerNotFoundIds(toResult(404, null, JSON.stringify({ error: { code: "league_not_found", message: "no", details: null } })))).toEqual([]);
  });

  it("gives a generic error for a 500 without a body", () => {
    const r = toResult(500, null, "");
    expect(r).toMatchObject({ kind: "error", code: "http_500" });
  });

  it("flags an unreadable 200 body", () => {
    const r = toResult(200, null, "{not json");
    expect(r).toMatchObject({ kind: "error", code: "bad_response" });
  });

  it("maps the proxy's api_unreachable to the unreachable state", () => {
    const r = toResult(
      502,
      null,
      JSON.stringify({ error: { code: "api_unreachable", message: "The app could not reach its API.", details: null } }),
    );
    expect(r.kind).toBe("unreachable");
  });

  it("keeps sleeper_unavailable (also 502) as an error", () => {
    const r = toResult(502, null, JSON.stringify({ error: { code: "sleeper_unavailable", message: "x", details: null } }));
    expect(r).toMatchObject({ kind: "error", code: "sleeper_unavailable" });
  });

  it("maps a 204 to ok with null data", () => {
    const r = toResult(204, null, "");
    expect(r).toMatchObject({ kind: "ok", data: null });
  });

  it("describes every contract error code", () => {
    for (const code of [
      "league_not_found",
      "team_not_found",
      "player_not_found",
      "week_not_available",
      "sleeper_not_found",
      "multiple_leagues",
      "no_team_selected",
      "not_a_sleeper_league",
      "offseason",
      "validation_error",
      "invalid_scoring",
      "invalid_roster_positions",
      "sleeper_unavailable",
      "internal_error",
    ]) {
      const d = describeError(code, code === "internal_error" ? 500 : 400);
      expect(d.title.length).toBeGreaterThan(3);
      expect(d.title).not.toBe("Request failed");
    }
  });
});
