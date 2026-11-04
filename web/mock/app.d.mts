// Types for the test-only contract mock.
export interface MockOptions {
  mode?: "production" | "private_test";
  seasonEndpoint?: boolean;
  seasonMode?: "regular" | "offseason" | "postseason" | "unknown";
  planned?: boolean;
  snapshot?: boolean;
}
export interface MockOut {
  status: number;
  body: unknown;
  headers?: Record<string, string>;
}
export function createMockApi(options?: MockOptions): {
  mode: "production" | "private_test";
  options: Required<Omit<MockOptions, "mode">>;
  handle(method: string, pathname: string, q: URLSearchParams, body?: unknown): MockOut;
};
export function startMockServer(options?: MockOptions & { port?: number; host?: string }): import("node:http").Server;
