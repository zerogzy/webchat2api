"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import webConfig from "@/constants/common-env";
import { getValidatedAuthSession } from "@/lib/auth-session";
import { cn } from "@/lib/utils";
import { clearStoredAuthSession, type StoredAuthSession } from "@/store/auth";

const adminNavItems = [
  { href: "/image", label: "试验" },
  { href: "/accounts", label: "号池管理" },
  { href: "/image-manager", label: "图片管理" },
  { href: "/logs", label: "日志管理" },
  { href: "/settings", label: "设置" },
];

const userNavItems = [{ href: "/image", label: "试验" }];

export function TopNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null | undefined>(undefined);

  useEffect(() => {
    let active = true;

    const load = async () => {
      if (pathname === "/login") {
        if (!active) {
          return;
        }
        setSession(null);
        return;
      }

      const storedSession = await getValidatedAuthSession();
      if (!active) {
        return;
      }
      setSession(storedSession);
    };

    void load();
    return () => {
      active = false;
    };
  }, [pathname]);

  const handleLogout = async () => {
    await clearStoredAuthSession();
    router.replace("/login");
  };

  if (pathname === "/login" || session === undefined || !session) {
    return null;
  }

  const navItems = session.role === "admin" ? adminNavItems : userNavItems;
  const roleLabel = session.role === "admin" ? "管理员" : "普通用户";
  const displayName = session.name.trim() || roleLabel;

  return (
    <header className="sticky top-0 z-40 rounded-b-[30px] border border-t-0 border-white/75 bg-card/82 shadow-[var(--shadow-lift)] backdrop-blur-xl sm:top-3 sm:rounded-[30px] sm:border-t">
      <div className="flex min-h-14 flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:px-5 sm:py-2 lg:px-6">
        <div className="flex items-center justify-between gap-2 sm:justify-start sm:gap-3">
          <Link
            href="/image"
            className="group inline-flex shrink-0 items-center gap-2 rounded-full py-1 pr-2 text-[15px] font-bold tracking-tight text-foreground transition hover:text-primary"
            aria-label="webchat2api 管理后台首页"
          >
            <Image
              src="/webchat2api-logo.png"
              alt="webchat2api logo"
              width={30}
              height={30}
              className="size-7.5 rounded-xl border border-white/80 bg-card object-contain shadow-sm transition group-hover:scale-105"
              priority
            />
            <span className="leading-none">webchat2api</span>
          </Link>
          <a
            href="https://github.com/zqbxdev/webchat2api"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full border border-transparent px-2.5 py-1 text-sm text-muted-foreground transition hover:border-white/75 hover:bg-card/70 hover:text-foreground hover:shadow-sm"
            aria-label="GitHub repository"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" className="size-4 fill-current">
              <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56v-2.14c-3.2.7-3.87-1.36-3.87-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.76 2.7 1.25 3.36.96.1-.75.4-1.25.73-1.54-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.04 0 0 .97-.31 3.17 1.18A10.9 10.9 0 0 1 12 6.05c.98 0 1.96.13 2.88.39 2.2-1.49 3.17-1.18 3.17-1.18.63 1.58.23 2.75.11 3.04.74.81 1.19 1.83 1.19 3.09 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.14v3.18c0 .31.21.68.8.56A11.51 11.51 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z" />
            </svg>
            <span className="hidden md:inline">webchat2api</span>
          </a>
          <button
            type="button"
            className="ml-auto shrink-0 rounded-full border border-white/70 bg-card/60 px-2.5 py-1 text-xs text-muted-foreground shadow-sm transition hover:bg-card hover:text-foreground sm:hidden"
            onClick={() => void handleLogout()}
          >
            退出
          </button>
        </div>
        <nav className="hide-scrollbar -mx-1 flex min-w-0 flex-1 gap-1 overflow-x-auto px-1 sm:mx-0 sm:flex-wrap sm:justify-center sm:gap-1.5 sm:overflow-visible sm:px-0">
          {navItems.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "relative shrink-0 whitespace-nowrap rounded-full border px-3 py-1.5 text-[13px] font-medium transition sm:text-[14px]",
                  active
                    ? "border-primary/10 bg-primary text-primary-foreground shadow-[0_14px_30px_-20px_oklch(0.22_0.024_65_/_0.8)] after:absolute after:inset-x-3 after:-bottom-1 after:h-px after:rounded-full after:bg-accent"
                    : "border-transparent text-muted-foreground hover:border-white/75 hover:bg-card/70 hover:text-foreground hover:shadow-sm",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="hidden items-center justify-end gap-2 sm:flex sm:flex-wrap sm:gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-white/75 bg-card/65 px-2.5 py-1 text-[11px] font-medium text-muted-foreground shadow-sm">
            <span className="size-1.5 rounded-full bg-accent" />
            {roleLabel}
            <span className="text-border">/</span>
            <span className="max-w-28 truncate text-foreground lg:max-w-40">{displayName}</span>
          </span>
          <span className="rounded-full border border-white/75 bg-card/65 px-2.5 py-1 text-[11px] font-medium text-muted-foreground shadow-sm">
            v{webConfig.appVersion}
          </span>
          <button
            type="button"
            className="rounded-full border border-transparent px-2.5 py-1 text-xs text-muted-foreground transition hover:border-white/75 hover:bg-card/70 hover:text-foreground hover:shadow-sm sm:text-sm"
            onClick={() => void handleLogout()}
          >
            退出
          </button>
        </div>
      </div>
    </header>
  );
}
