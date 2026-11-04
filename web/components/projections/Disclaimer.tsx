const DEFAULT =
  "No guarantees: these are probabilities from simulations of possible outcomes, not predictions of what will happen. Use them as one input to your own decision.";

/** Plain "no guarantees" language for start/sit, waivers and trades. */
export function Disclaimer({ text }: { text?: string | null }) {
  const body = text?.trim() || DEFAULT;
  return (
    <p className="mt-6 rounded-lg border border-line bg-surface-2 px-4 py-3 text-sm" data-testid="disclaimer">
      {/^no guarantees/i.test(body) ? body : `No guarantees. ${body}`}
    </p>
  );
}
