"use client";

import "./globals.css";

/** Last-resort boundary when the root layout itself fails. */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body>
        <main className="mx-auto max-w-xl px-4 py-16">
          <h1 className="text-2xl font-bold">The app could not load</h1>
          <p className="mt-3 text-muted">Something went wrong before the page could be shown.</p>
          <button type="button" className="btn btn-primary mt-6" onClick={reset}>
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
