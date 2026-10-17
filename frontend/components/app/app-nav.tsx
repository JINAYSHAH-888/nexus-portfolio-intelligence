"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { LogOut, User } from "lucide-react";
import { logout as apiLogout, getProfile } from "@/lib/api-client";
import { useAuthStore } from "@/lib/nippon-auth-store";
import ElectricMark from "@/components/brand/electric-mark";
import ThemeToggle from "@/components/theme/theme-toggle";

/* In analysis order: hold schemes, see the overlap, score it, price the
 * risk, project the retirement. No index numbers -- the order carries it. */
const LINKS = [
  { name: "Overview", path: "/dashboard" },
  { name: "Schemes", path: "/schemes" },
  { name: "Overlap", path: "/analysis/overlap" },
  { name: "PLOS", path: "/analysis/plos" },
  { name: "Risk", path: "/analysis/risk" },
  { name: "ARISE", path: "/analysis/arise" },
];

/**
 * The app's top bar. Same identity as the landing nav -- the electric N --
 * and full width.
 *
 * On wide screens it is one row. Below `lg` the tabs drop to their own row
 * and scroll horizontally with faded edges, rather than being clipped
 * mid-word as the previous single-row bar was at laptop widths.
 */
export function AppNav() {
  const pathname = usePathname();
  const router = useRouter();
  const clearAuth = useAuthStore((s) => s.clear);
  // Long staleTime: the name rarely changes and this runs on every screen.
  const { data: profile } = useQuery({
    queryKey: ["profile"],
    queryFn: getProfile,
    staleTime: 5 * 60_000,
    retry: 0,
  });

  async function handleLogout() {
    await apiLogout();
    clearAuth();
    router.push("/");
  }

  return (
    // Floating liquid-glass bar (owner direction): content scrolls BEHIND the
    // frosted panel rather than under an opaque strip.
    <header className="sticky top-0 z-30 select-none pt-3">
      <nav aria-label="Primary" className="grid-page">
        <div className="glass col-span-12 flex flex-wrap items-center gap-x-6 px-4 [&>*]:relative [&>*]:z-[2]">
          <Link
            href="/dashboard"
            className="-ml-3 flex h-16 shrink-0 items-center gap-1"
            aria-label="Nexus Portfolio Intelligence — overview"
          >
            <ElectricMark size={56} />
            <span className="font-display text-step-0 font-semibold uppercase tracking-tight text-text">
              Nexus<span className="hidden xl:inline"> Portfolio Intelligence</span>
            </span>
          </Link>

          <ul className="tab-strip order-last -mx-2 flex h-12 w-full items-stretch overflow-x-auto lg:order-none lg:mx-0 lg:h-16 lg:w-auto lg:flex-1 lg:justify-center">
            {LINKS.map((link) => {
              const active = pathname === link.path || pathname?.startsWith(link.path + "/");
              return (
                <li key={link.path} className="shrink-0">
                  <Link
                    href={link.path}
                    // aria-current tells a screen reader which page this is;
                    // the underline only tells people who can see it.
                    aria-current={active ? "page" : undefined}
                    className="flex h-full min-h-[44px] items-center px-1"
                  >
                    {/* The current screen is an ink pill with a red dot: in
                        the app, black carries structure and red is the accent. */}
                    <span
                      className={`flex h-9 items-center gap-2 rounded-full px-4 font-mono text-step--2 uppercase tracking-wider transition-colors duration-200 ${
                        active
                          ? "bg-[var(--mark)] text-[var(--mark-fg)] shadow-[0_6px_20px_-8px_var(--mark)]"
                          : "text-text-secondary hover:text-text"
                      }`}
                    >
                      {active && <span className="h-1.5 w-1.5 rounded-full bg-[var(--mark-dot)]" aria-hidden="true" />}
                      {link.name}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>

          <div className="ml-auto flex h-16 shrink-0 items-center gap-2 lg:ml-0">
            <ThemeToggle />
            <Link
              href="/profile"
              className="btn"
              aria-label={profile?.full_name ? `Investor profile for ${profile.full_name}` : "Investor profile"}
            >
              <User className="h-4 w-4" aria-hidden="true" strokeWidth={1.5} />
              <span className="hidden max-w-[14ch] truncate md:inline">{profile?.full_name || "Profile"}</span>
            </Link>
            {/* Icon-only, so the accessible name comes from aria-label. */}
            <button type="button" onClick={handleLogout} aria-label="Sign out" className="btn !w-11 !px-0">
              <LogOut className="h-4 w-4" aria-hidden="true" strokeWidth={1.5} />
            </button>
          </div>
        </div>
      </nav>
    </header>
  );
}
