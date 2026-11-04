import { describe, expect, it } from "vitest";
import { deriveApprovalMode } from "@/components/status/approval-mode";
import { readApproval, type ApiResult } from "@/lib/api/result";
import type { ApprovalResponse } from "@/lib/api/types";

const PRODUCTION_OFF: ApprovalResponse = {
  environment: "production",
  serving: false,
  approval_status: "not approved",
  label: "not approved",
  reason: "no_live_model",
  message: "No approved model. Projections are off until a model passes its pre-registered test.",
  checked_at: "2026-11-04T20:00:00Z",
  run: null,
  live_model_version: null,
};

const ok = (data: ApprovalResponse): ApiResult<ApprovalResponse> => ({ kind: "ok", status: 200, data, approval: null });

describe("deriveApprovalMode", () => {
  it("is loading before /meta/approval answers", () => {
    expect(deriveApprovalMode(null, null, null).kind).toBe("loading");
  });

  it("is no_approved_model in production without a live model", () => {
    const m = deriveApprovalMode(ok(PRODUCTION_OFF), null, null);
    expect(m).toMatchObject({ kind: "no_approved_model", message: PRODUCTION_OFF.message });
  });

  it("is not_approved in private_test, serving or not", () => {
    const serving = deriveApprovalMode(
      ok({ ...PRODUCTION_OFF, environment: "private_test", serving: true, reason: "private_test_replay" }),
      null,
      null,
    );
    expect(serving).toMatchObject({ kind: "not_approved", serving: true, label: "not approved" });
    const empty = deriveApprovalMode(
      ok({ ...PRODUCTION_OFF, environment: "private_test", reason: "no_private_test_snapshot" }),
      null,
      null,
    );
    expect(empty).toMatchObject({ kind: "not_approved", serving: false });
  });

  it("never reports approved in private_test, even if the body claims it", () => {
    const m = deriveApprovalMode(
      ok({ ...PRODUCTION_OFF, environment: "private_test", serving: true, approval_status: "approved", label: "approved" }),
      null,
      null,
    );
    expect(m.kind).toBe("not_approved");
    if (m.kind !== "not_approved") throw new Error();
    expect(m.label).toBe("not approved");
  });

  it("is approved only for production, serving, approval_status approved", () => {
    const m = deriveApprovalMode(
      ok({ ...PRODUCTION_OFF, serving: true, approval_status: "approved", label: "approved", reason: "approved_live_model", live_model_version: "chain_v2" }),
      null,
      null,
    );
    expect(m).toMatchObject({ kind: "approved", liveModelVersion: "chain_v2" });
  });

  it("drops approved as soon as any response says not approved or blocked", () => {
    const approvedMeta = ok({ ...PRODUCTION_OFF, serving: true, approval_status: "approved", label: "approved" });
    const notApproved = readApproval({ approval_status: "not approved", label: "not approved" }, "not-approved");
    expect(deriveApprovalMode(approvedMeta, notApproved, null).kind).toBe("not_approved");
    expect(deriveApprovalMode(approvedMeta, null, "No approved model.").kind).toBe("no_approved_model");
  });

  it("uses a gate 503 seen on a route when /meta/approval failed", () => {
    const failed: ApiResult<ApprovalResponse> = { kind: "unreachable", status: 0, message: "down" };
    expect(deriveApprovalMode(failed, null, "No approved model.").kind).toBe("no_approved_model");
    expect(deriveApprovalMode(failed, null, null).kind).toBe("unavailable");
  });
});
