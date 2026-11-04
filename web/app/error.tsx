"use client";

import Link from "next/link";
import { useEffect } from "react";
import { StatePanel } from "@/components/states/StatePanel";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") console.error(error);
  }, [error]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">This page hit a problem</h1>
      <StatePanel
        title="Something went wrong while showing this page"
        tone="danger"
        role="alert"
        testId="route-error"
        actions={
          <>
            <button type="button" className="btn btn-primary" onClick={reset}>
              Try again
            </button>
            <Link href="/" className="btn btn-secondary">
              Go to the home page
            </Link>
          </>
        }
      >
        <p>The rest of the app still works. Trying again usually fixes a one-off problem.</p>
        {error.digest ? <p className="font-mono text-xs opacity-80">Reference: {error.digest}</p> : null}
      </StatePanel>
    </div>
  );
}
