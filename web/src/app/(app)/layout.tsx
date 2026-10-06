import Link from "next/link";
import type { ReactNode } from "react";

import { SignOutButton } from "@/components/sign-out-button";
import { getAccount } from "@/lib/api";

export default async function AppLayout({ children }: { children: ReactNode }) {
  // Sends anyone without a session to the sign-in page.
  const account = await getAccount();

  return (
    <>
      <header className="sticky top-0 z-10 border-b border-edge bg-page/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex min-w-0 items-baseline gap-3">
            <Link href="/leaks" className="text-base font-semibold tracking-tight">
              Cosmos
            </Link>
            <span className="truncate text-sm text-ink-2">{account.tenant_name}</span>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="hidden text-sm text-ink-2 sm:inline">{account.name}</span>
            <SignOutButton />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 pb-20 pt-8 sm:px-6">{children}</main>
    </>
  );
}
