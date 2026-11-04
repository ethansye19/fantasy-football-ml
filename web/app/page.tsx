import Link from "next/link";
import { FeatureGrid, StatusSummary } from "@/components/home/HomeStatus";

export default function HomePage() {
  return (
    <div className="space-y-10">
      <section className="space-y-4 pt-2">
        <p className="text-sm font-semibold uppercase tracking-wider text-accent">Fantasy football analytics</p>
        <h1 className="max-w-3xl text-3xl font-bold leading-tight tracking-tight sm:text-4xl">
          Projections as ranges, under your league&apos;s rules.
        </h1>
        <p className="max-w-2xl text-lg text-muted">
          Each player gets a floor, a median and a ceiling from thousands of simulated games, with the chance of a big
          week or a poor one. Your scoring is applied to the simulated stat lines, so custom leagues need no special
          model.
        </p>
        <div className="flex flex-wrap gap-2 pt-1">
          <Link href="/setup" className="btn btn-primary">
            Set up your league
          </Link>
          <Link href="/about" className="btn btn-secondary">
            How it works
          </Link>
        </div>
      </section>

      <StatusSummary />

      <section aria-labelledby="features-heading" className="space-y-4">
        <h2 id="features-heading" className="text-xl font-semibold">
          Tools
        </h2>
        <FeatureGrid />
      </section>
    </div>
  );
}
