"use client";

import { forwardRef, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";

/** Error keys like "scoring.pass_td" become element ids like "scoring-pass_td". */
export function fieldId(key: string): string {
  return `f-${key.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

function describedBy(id: string, hint?: ReactNode, error?: string): string | undefined {
  const ids = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean);
  return ids.length ? ids.join(" ") : undefined;
}

interface BaseProps {
  id: string;
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  className?: string;
}

export const TextField = forwardRef<HTMLInputElement, BaseProps & Omit<InputHTMLAttributes<HTMLInputElement>, "id">>(
  function TextField({ id, label, hint, error, className = "", ...rest }, ref) {
    return (
      <div className={className}>
        <label htmlFor={id} className="mb-1 block text-sm font-medium">
          {label}
        </label>
        {hint ? (
          <p id={`${id}-hint`} className="mb-1 text-xs text-muted">
            {hint}
          </p>
        ) : null}
        <input
          ref={ref}
          id={id}
          className="field"
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, hint, error)}
          {...rest}
        />
        {error ? (
          <p id={`${id}-error`} className="mt-1 text-sm font-medium text-danger-ink">
            {error}
          </p>
        ) : null}
      </div>
    );
  },
);

export function TextAreaField({
  id,
  label,
  hint,
  error,
  className = "",
  ...rest
}: BaseProps & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id">) {
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block text-sm font-medium">
        {label}
      </label>
      {hint ? (
        <p id={`${id}-hint`} className="mb-1 text-xs text-muted">
          {hint}
        </p>
      ) : null}
      <textarea
        id={id}
        className="field min-h-24 font-mono text-sm"
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
        {...rest}
      />
      {error ? (
        <p id={`${id}-error`} className="mt-1 text-sm font-medium text-danger-ink">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Listed at the top of a form after a failed submit; each item links to its field. */
export const ErrorSummary = forwardRef<
  HTMLDivElement,
  { items: { href: string; text: string }[]; formError?: string | null; title?: string }
>(function ErrorSummary({ items, formError, title }, ref) {
  if (!items.length && !formError) return null;
  return (
    <div
      ref={ref}
      tabIndex={-1}
      role="alert"
      data-testid="error-summary"
      className="rounded-xl border border-danger-line bg-danger-bg p-4 text-danger-ink outline-none"
    >
      <h3 className="font-semibold">{items.length ? "Please fix the following" : (title ?? "That did not work")}</h3>
      {formError ? <p className="mt-1 text-sm">{formError}</p> : null}
      {items.length ? (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
          {items.map((item) => (
            <li key={item.href + item.text}>
              <a href={item.href} className="underline underline-offset-2">
                {item.text}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
});
