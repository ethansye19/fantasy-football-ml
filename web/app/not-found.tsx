import Link from "next/link";
import { EmptyState } from "@/components/states/States";

export default function NotFound() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">Page not found</h1>
      <EmptyState
        title="There is no page at this address"
        actions={
          <Link href="/" className="btn btn-primary">
            Go to the home page
          </Link>
        }
      >
        <p>Check the address, or use the menu to find what you need.</p>
      </EmptyState>
    </div>
  );
}
