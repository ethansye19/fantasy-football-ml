import Link from "next/link";
import { APP_NAME } from "@/lib/nav";
import { ThemeReset } from "./ThemeToggle";

export function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-line bg-surface">
      <div className="mx-auto grid max-w-6xl gap-4 px-4 py-8 text-sm text-muted sm:grid-cols-[1fr_auto]">
        <div className="space-y-2">
          <p>
            <span className="font-semibold text-ink">{APP_NAME}</span> shows ranges of possible outcomes from
            simulations. Nothing here is a promise about what will happen, and nothing here is betting advice.
          </p>
          <p>
            Data from nflverse (CC-BY-4.0) and league data from Sleeper. Personal, non-commercial use.{" "}
            <Link href="/about/attribution" className="link">
              Full attribution
            </Link>
            .
          </p>
        </div>
        <ul className="flex flex-wrap items-start gap-x-4 gap-y-2 sm:justify-end">
          <li>
            <Link href="/about/model-card" className="link">
              Model card
            </Link>
          </li>
          <li>
            <Link href="/about/limitations" className="link">
              Limitations
            </Link>
          </li>
          <li>
            <ThemeReset />
          </li>
        </ul>
      </div>
    </footer>
  );
}
