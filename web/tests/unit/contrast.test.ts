import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "..", "..", "app", "globals.css"), "utf8");

function block(selector: string): string {
  const start = css.indexOf(selector);
  if (start < 0) throw new Error(`missing ${selector}`);
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}") {
      depth--;
      if (depth === 0) return css.slice(open + 1, i);
    }
  }
  throw new Error("unbalanced");
}

function tokens(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-f]{6})\s*;/gi)) out[m[1] as string] = (m[2] as string).toLowerCase();
  return out;
}

function luminance(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (lin[0] as number) + 0.7152 * (lin[1] as number) + 0.0722 * (lin[2] as number);
}

function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const light = tokens(block(":root {"));
const darkMedia = tokens(block(':root:not([data-theme="light"])'));
const darkAttr = tokens(block(':root[data-theme="dark"]'));

// [foreground, background, minimum ratio]
const PAIRS: [string, string, number][] = [
  ["text", "bg", 4.5],
  ["text", "surface", 4.5],
  ["text", "surface-2", 4.5],
  ["text-muted", "bg", 4.5],
  ["text-muted", "surface", 4.5],
  ["text-muted", "surface-2", 4.5],
  ["accent", "surface", 4.5],
  ["accent", "bg", 4.5],
  ["accent-contrast", "accent", 4.5],
  ["accent-contrast", "accent-hover", 4.5],
  ["text", "accent-soft", 4.5],
  ["warn-text", "warn-bg", 4.5],
  ["info-text", "info-bg", 4.5],
  ["danger-text", "danger-bg", 4.5],
  ["boom", "boom-bg", 4.5],
  ["bust", "bust-bg", 4.5],
  ["boom", "surface", 4.5],
  ["bust", "surface", 4.5],
  // --pos-* are categorical marker colors, always shown next to the position
  // text; they come from a validated categorical palette (CVD and normal-vision
  // separation checked with the dataviz validator), so they are not text pairs.
  // non-text UI parts: 3:1
  ["border-strong", "surface", 3],
  ["warn-border", "warn-bg", 3],
  ["strip-median", "strip-band", 3],
  ["focus", "surface", 3],
  ["focus", "bg", 3],
];

describe("theme contrast", () => {
  it("dark tokens are identical in the media query and the data-theme block", () => {
    expect(darkMedia).toEqual(darkAttr);
  });

  for (const [name, theme] of [
    ["light", light],
    ["dark", darkAttr],
  ] as const) {
    it(`${name} theme meets the contrast minimums`, () => {
      const failures: string[] = [];
      for (const [fg, bg, min] of PAIRS) {
        const a = theme[fg];
        const b = theme[bg];
        if (!a || !b) {
          failures.push(`${fg}/${bg}: missing token`);
          continue;
        }
        const r = ratio(a, b);
        if (r < min) failures.push(`${fg} on ${bg}: ${r.toFixed(2)} < ${min}`);
      }
      expect(failures).toEqual([]);
    });
  }
});
