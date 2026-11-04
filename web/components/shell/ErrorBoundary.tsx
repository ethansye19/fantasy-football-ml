"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Rendered instead of the children after an error; null hides the part. */
  fallback?: ReactNode;
  name?: string;
}

/** Keeps a failing widget (a banner, a chart) from taking the page down. */
export class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    if (process.env.NODE_ENV !== "production") {
      console.error(`[${this.props.name ?? "ErrorBoundary"}]`, error, info.componentStack);
    }
  }

  render(): ReactNode {
    if (this.state.failed) return this.props.fallback ?? null;
    return this.props.children;
  }
}
