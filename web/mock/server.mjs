// Run the contract mock:  node mock/server.mjs
//   MOCK_PORT=8765  MOCK_MODE=private_test|production
//   MOCK_SEASON=on|off  MOCK_SEASON_MODE=regular|offseason|postseason
//   MOCK_PLANNED=on|off (planned /meta endpoints)  MOCK_SNAPSHOT=on|off (private_test only)
import { startMockServer } from "./app.mjs";

const flag = (name, def) => {
  const v = process.env[name];
  return v === undefined ? def : !/^(0|off|false|no)$/i.test(v);
};

const port = Number(process.env.MOCK_PORT ?? 8765);
const mode = process.env.MOCK_MODE === "production" ? "production" : "private_test";
const server = startMockServer({
  port,
  mode,
  seasonEndpoint: flag("MOCK_SEASON", true),
  seasonMode: process.env.MOCK_SEASON_MODE ?? "regular",
  planned: flag("MOCK_PLANNED", true),
  snapshot: flag("MOCK_SNAPSHOT", true),
});

server.on("listening", () => console.log(`ffml contract mock (${mode}) on http://127.0.0.1:${port}`));
const stop = () => server.close(() => process.exit(0));
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
