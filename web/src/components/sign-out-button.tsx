"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function SignOutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function signOut() {
    setPending(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      // Whether or not the request got through, leave the signed-in pages.
      router.replace("/login");
      router.refresh();
    }
  }

  return (
    <button
      type="button"
      onClick={signOut}
      disabled={pending}
      className="rounded-md px-2 py-1 text-sm text-ink-2 hover:bg-wash hover:text-ink disabled:opacity-60"
    >
      Sign out
    </button>
  );
}
