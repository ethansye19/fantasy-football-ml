"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { APP_NAME, NAV_ITEMS, isActive } from "@/lib/nav";
import { LogoMark } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

export function SiteHeader() {
  const pathname = usePathname() ?? "/";
  const [open, setOpen] = useState(false);
  const [openedAt, setOpenedAt] = useState(pathname);
  const menuId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Close the mobile menu after navigating.
  if (open && openedAt !== pathname) {
    setOpen(false);
  }

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4">
        <Link href="/" className="flex shrink-0 items-center gap-2 rounded-md font-bold tracking-tight text-ink">
          <LogoMark />
          <span className="text-[1.05rem]">{APP_NAME}</span>
        </Link>

        <nav aria-label="Main" className="ml-auto hidden lg:block">
          <ul className="flex items-center gap-1">
            {NAV_ITEMS.map((item) => {
              const active = isActive(pathname, item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={`rounded-md px-3 py-2 text-[0.9375rem] font-medium ${
                      active ? "bg-accent-soft text-ink" : "text-muted hover:bg-surface-2 hover:text-ink"
                    }`}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="ml-auto flex items-center gap-1 lg:ml-2">
          <ThemeToggle />
          <button
            ref={buttonRef}
            type="button"
            className="btn btn-ghost h-10 w-10 p-0 lg:hidden"
            aria-expanded={open}
            aria-controls={menuId}
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => {
              setOpenedAt(pathname);
              setOpen((v) => !v);
            }}
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              {open ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
            </svg>
          </button>
        </div>
      </div>

      <nav id={menuId} aria-label="Main" hidden={!open} className="border-t border-line lg:hidden">
        <ul className="mx-auto grid max-w-6xl gap-1 px-4 py-2">
          {NAV_ITEMS.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  onClick={() => setOpen(false)}
                  className={`block rounded-md px-3 py-2.5 font-medium ${
                    active ? "bg-accent-soft text-ink" : "text-ink hover:bg-surface-2"
                  }`}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </header>
  );
}
