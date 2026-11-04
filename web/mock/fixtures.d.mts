// Types for the test-only fixtures, so TypeScript tests can import them.
export const NOT_APPROVED_NOTICE: string;
export const FIXTURE_NOTICE: string;
export const NO_GUARANTEES: string;
export const NO_APPROVED_MODEL_MESSAGE: string;
export const NO_PRIVATE_SNAPSHOT_MESSAGE: string;
export const FIXTURE_RUN: import("../lib/api/types").RunInfo;
export const REMAINING_WEEKS: number[];
export function rng(seed: number): () => number;
export const PLAYERS: (import("../lib/api/types").PlayerRow & { injury_status: string | null; bye_week: number })[];
export function playerRow(p: unknown, roster?: unknown): import("../lib/api/types").PlayerRow;
export function findPlayer(id: string): (import("../lib/api/types").PlayerRow & { bye_week: number }) | null;
export const SLEEPER_USERS: Record<string, { user: { user_id: string; username: string; display_name: string }; leagues: import("../lib/api/types").SleeperLeagueSummary[] }>;
export const ATTRIBUTION: import("../lib/api/types").AttributionResponse;
export const MODEL_CARD: import("../lib/api/types").ModelCardResponse & { status_summary: string };
