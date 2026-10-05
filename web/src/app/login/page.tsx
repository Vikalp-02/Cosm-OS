import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SignInForm } from "@/components/sign-in-form";
import { isSignedIn } from "@/lib/api";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage() {
  if (await isSignedIn()) redirect("/leaks");

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-semibold tracking-tight">Cosmos</h1>
        <p className="mt-1 text-sm text-ink-2">Find where revenue is leaking, and why.</p>
        <div className="mt-8 rounded-xl border border-edge bg-surface p-6">
          <SignInForm />
        </div>
      </div>
    </main>
  );
}
