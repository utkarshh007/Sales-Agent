"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, Suspense, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";
import { LogoMark } from "./Logo";

const MeContext = createContext<Me | null>(null);
export const useMe = () => useContext(MeContext);
export const canEdit = (me: Me | null) => me?.role === "analyst" || me?.role === "admin";

const NAV = [
  { href: "/", label: "Today" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/tenders", label: "Tenders" },
  { href: "/reviews", label: "Review queue" },
  { href: "/pipeline", label: "Bid pipeline" },
  { href: "/insights", label: "Insights" },
  { href: "/profile", label: "Company profile" },
  { href: "/sources", label: "Sources & rules" },
];

function NavLinks({ onNavigate }: { onNavigate: () => void }) {
  const pathname = usePathname();
  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  return (
    <ul className="space-y-0.5">
      {NAV.map((n) => (
        <li key={n.href}>
          <Link href={n.href} onClick={onNavigate}
            className={`block rounded-md px-3 py-1.5 text-[15px] ${active(n.href) ? "bg-teal-soft font-semibold text-teal" : "text-ink hover:bg-sunken"}`}
            aria-current={active(n.href) ? "page" : undefined}>
            {n.label}
          </Link>
        </li>
      ))}
    </ul>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api<Me>("/auth/me").then((m) => {
      setMe(m);
      if (m.mfa_setup_required && window.location.pathname !== "/security") {
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.href = "/security";
      }
    }).catch(() => {});
  }, []);

  async function signOut() {
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    // full navigation on purpose: drops all client state from the signed-out session
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login";
  }

  return (
    <MeContext.Provider value={me}>
      <div className="min-h-screen md:grid md:grid-cols-[13.5rem_1fr]">
        <aside className="border-b border-line bg-surface md:sticky md:top-0 md:h-screen md:border-b-0 md:border-r">
          <div className="flex items-center justify-between px-5 py-4 md:block">
            <Link href="/" className="flex items-center gap-2.5" aria-label="Trever RFP Portal, home">
              <LogoMark size={34} className="shrink-0" />
              <span className="leading-tight">
                <span className="block font-serif text-lg font-semibold tracking-tight">Trever</span>
                <span className="block text-xs text-muted">RFP Portal</span>
              </span>
            </Link>
            <button className="rounded-md border border-line px-2.5 py-1 text-sm md:hidden" onClick={() => setOpen(!open)}
              aria-expanded={open} aria-controls="main-nav">Menu</button>
          </div>
          <nav id="main-nav" className={`${open ? "block" : "hidden"} px-3 pb-4 md:block`}>
            <Suspense fallback={<ul className="space-y-0.5">{NAV.map((n) => <li key={n.href}><Link href={n.href} className="block px-3 py-1.5">{n.label}</Link></li>)}</ul>}>
              <NavLinks onNavigate={() => setOpen(false)} />
            </Suspense>
            {me && (
              <div className="mt-6 border-t border-line px-3 pt-4 text-sm md:absolute md:bottom-5 md:left-3 md:right-3 md:mt-0">
                <p className="truncate font-medium" title={me.email}>{me.email}</p>
                <p className="text-muted">{me.role === "admin" ? "Administrator" : me.role === "analyst" ? "Analyst" : "Viewer"}</p>
                <div className="mt-2 flex gap-4">
                  <Link href="/security" onClick={() => setOpen(false)} className="text-teal underline-offset-2 hover:underline">
                    Security{!me.mfa_enabled && <span className="sr-only"> (two-factor authentication off)</span>}
                  </Link>
                  <button onClick={signOut} className="text-teal underline-offset-2 hover:underline">Sign out</button>
                </div>
              </div>
            )}
          </nav>
        </aside>
        <main className="min-w-0 px-4 py-6 md:px-10 md:py-8">{children}</main>
      </div>
    </MeContext.Provider>
  );
}
