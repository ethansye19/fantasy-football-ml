/**
 * Copy rules for the whole UI (D-007, D-097, D-099, PROMPT_1):
 * no "lock", "guaranteed" or "can't miss" wording, no restricted ranking
 * sources, and no approval claims in our own copy.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..", "..");
const DIRS = ["app", "components", "lib", "mock"];

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p));
    else if (/\.(tsx?|mjs|json|css)$/.test(name)) out.push(p);
  }
  return out;
}

const SOURCES = DIRS.filter((d) => existsSync(join(ROOT, d)))
  .flatMap((d) => files(join(ROOT, d)))
  .map((p) => ({
  path: relative(ROOT, p).replace(/\\/g, "/"),
  text: readFileSync(p, "utf8"),
}));

const BANNED: [string, RegExp][] = [
  ["lock", /\block(s|ed)?\b/i],
  ["guaranteed", /\bguarantee(d)?\b(?!s)/i],
  ["can't miss", /can['’]?t[\s-]+miss/i],
  ["sure thing", /\bsure[\s-]+thing\b/i],
  ["FantasyPros", /fantasy\s*pros/i],
  ["ECR", /\bECR\b/],
  ["consensus", /\bconsensus\b/i],
  ["sportsbook", /\b(sportsbook|parlay|wager)\b/i],
];

describe("UI wording", () => {
  it("scans a non-trivial set of files", () => {
    expect(SOURCES.length).toBeGreaterThan(20);
  });

  for (const [name, re] of BANNED) {
    it(`never uses ${name}`, () => {
      const hits = SOURCES.filter((s) => re.test(s.text)).map((s) => s.path);
      expect(hits).toEqual([]);
    });
  }

  it("only says 'approved' as 'not approved', 'no approved' or in code that checks the API value", () => {
    // Allowed: negations, and code that names or compares the API value.
    const ALLOWED = [
      /not[\s-]approved/gi,
      /\bno approved\b/gi,
      /\bno model is approved\b/gi,
      /NotApproved\w*/g,
      /["'`]approved["'`]/g,
      /\.approved\b/g,
      /\bapproved\s*[:?]/g,
      /\w+_approved\b|\bapproved_\w+/g,
      /\bapproved(?=[A-Z])/g,
      /no-approved-\w+/g,
      /\^approved\$/g,
    ];
    const offenders: string[] = [];
    for (const s of SOURCES) {
      if (s.path.startsWith("lib/api/") || s.path.includes("approval-mode") || s.path.startsWith("mock/")) continue;
      s.text.split(/\r?\n/).forEach((line, i) => {
        // comments are for developers, not shown in the UI
        if (/^\s*(\/\/|\/\*\*?|\*)/.test(line)) return;
        let rest = line;
        for (const re of ALLOWED) rest = rest.replace(re, "");
        if (/\bapproved\b/i.test(rest)) offenders.push(`${s.path}:${i + 1}: ${line.trim()}`);
      });
    }
    expect(offenders).toEqual([]);
  });
});
