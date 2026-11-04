import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { ErrorBoundary } from "@/components/shell/ErrorBoundary";
import { SiteFooter } from "@/components/shell/SiteFooter";
import { SiteHeader } from "@/components/shell/SiteHeader";
import { ApprovalBanner } from "@/components/status/ApprovalBanner";
import { SeasonBanner } from "@/components/status/SeasonBanner";
import { StatusProvider } from "@/components/status/StatusProvider";
import { LeagueProvider } from "@/lib/league";
import { APP_NAME } from "@/lib/nav";
import { THEME_INIT_SCRIPT } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: APP_NAME, template: `%s · ${APP_NAME}` },
  description:
    "Fantasy football projections as ranges: floor, median, ceiling and boom/bust chances under your league's scoring.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f5f1" },
    { media: "(prefers-color-scheme: dark)", color: "#0d1310" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="flex min-h-screen flex-col">
        <a
          href="#main"
          className="sr-only-focusable absolute left-4 top-2 z-50 rounded-md bg-accent px-3 py-2 font-semibold text-on-accent"
        >
          Skip to content
        </a>
        <StatusProvider>
          <LeagueProvider>
            <SiteHeader />
            <ErrorBoundary name="banners">
              <ApprovalBanner />
              <SeasonBanner />
            </ErrorBoundary>
            <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 outline-none sm:py-8">
              {children}
            </main>
            <SiteFooter />
          </LeagueProvider>
        </StatusProvider>
      </body>
    </html>
  );
}
