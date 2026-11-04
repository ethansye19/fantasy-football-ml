"use client";

import Link from "next/link";
import { useStatus } from "@/components/status/StatusProvider";
import { DEFINITIONS } from "@/lib/content";
import { Section } from "./AboutShell";

const CHAIN = [
  ["Team plays", "How many plays each offense runs and how often it passes."],
  ["Opportunity", "Each player's share of targets, carries and dropbacks."],
  ["Efficiency", "Yards per target and per carry, catch rate."],
  ["Touchdowns", "The team's expected touchdowns, split by each player's share."],
  ["Simulation", "Thousands of correlated game simulations produce stat lines."],
  ["Your scoring", "Your league's rules turn each simulated stat line into points."],
] as const;

export function AboutOverview() {
  const { approval } = useStatus();
  return (
    <div className="space-y-10">
      <Section title="What this app does" id="about-what">
        <p className="max-w-3xl leading-relaxed">
          It estimates a range of fantasy outcomes for each player, not a single number. You see a floor, a median and a
          ceiling, the chance of a big week (boom) and the chance of a poor one (bust), and the main reasons behind each
          projection. Start/sit, waivers, trades and rest-of-season rankings are built on the same simulations, under
          your league&apos;s own scoring and roster rules.
        </p>
        {approval.kind === "no_approved_model" || approval.kind === "not_approved" ? (
          <p className="max-w-3xl rounded-lg border border-info-line bg-info-bg px-3 py-2 text-sm text-info-ink">
            Right now no model has passed its pre-registered test, so projection features are off in the real app.{" "}
            <Link href="/about/model-card" className="font-semibold underline underline-offset-2">
              See the model status
            </Link>
            .
          </p>
        ) : null}
      </Section>

      <Section title="Football first, fantasy points last" id="about-chain">
        <p className="max-w-3xl leading-relaxed">
          The model predicts football before fantasy. Each step passes a distribution, not a point, to the next one:
        </p>
        <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {CHAIN.map(([title, text], i) => (
            <li key={title} className="card flex gap-3 p-4">
              <span
                aria-hidden="true"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-bold text-on-accent"
              >
                {i + 1}
              </span>
              <span>
                <span className="block font-semibold">{title}</span>
                <span className="block text-sm text-muted">{text}</span>
              </span>
            </li>
          ))}
        </ol>
        <p className="max-w-3xl text-sm text-muted">
          Because scoring is applied last, any league&apos;s scoring works without retraining. Teammates and opponents
          share game-level draws, so their outcomes stay correlated.
        </p>
      </Section>

      <Section title="Reading the numbers" id="about-numbers">
        <dl className="grid max-w-3xl gap-x-6 gap-y-2 sm:grid-cols-[8rem_1fr]">
          {Object.entries(DEFINITIONS).map(([term, text]) => (
            <div key={term} className="contents">
              <dt className="font-semibold capitalize">{term}</dt>
              <dd className="mb-2 text-muted sm:mb-0">{text}</dd>
            </div>
          ))}
        </dl>
        <p className="max-w-3xl text-sm text-muted">
          These are probabilities from simulations of possible outcomes. They are not promises, and they are one input
          to your own decisions.
        </p>
      </Section>

      <Section title="More" id="about-more">
        <ul className="grid gap-3 sm:grid-cols-3">
          {[
            ["/about/model-card", "Model card", "Status, method, evaluation and registry."],
            ["/about/limitations", "Limitations", "What the model and the app do not do well."],
            ["/about/attribution", "Data and attribution", "Sources, licenses and credits."],
          ].map(([href, title, text]) => (
            <li key={href}>
              <Link href={href as string} className="card block h-full p-4 hover:border-line-strong">
                <span className="block font-semibold">{title}</span>
                <span className="block text-sm text-muted">{text}</span>
              </Link>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
