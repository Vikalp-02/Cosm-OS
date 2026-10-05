"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

const FIELD =
  "mt-1.5 block w-full rounded-lg border border-edge bg-page px-3 py-2 text-base" +
  " placeholder:text-ink-3 focus-visible:outline-offset-0";

export function SignInForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      if (response.ok) {
        router.replace("/leaks");
        router.refresh();
        return;
      }
      const body: unknown = await response.json().catch(() => null);
      const detail = (body as { detail?: unknown } | null)?.detail;
      setError(
        typeof detail === "string" ? detail : "We couldn't sign you in. Please try again.",
      );
    } catch {
      setError("We couldn't reach the service. Check your connection and try again.");
    }
    setPending(false);
  }

  return (
    <form onSubmit={onSubmit} noValidate={false}>
      <label className="block text-sm font-medium" htmlFor="email">
        Email
      </label>
      <input
        id="email"
        name="email"
        type="email"
        autoComplete="username"
        required
        autoFocus
        className={FIELD}
      />

      <label className="mt-4 block text-sm font-medium" htmlFor="password">
        Password
      </label>
      <input
        id="password"
        name="password"
        type="password"
        autoComplete="current-password"
        required
        className={FIELD}
      />

      <p role="alert" aria-live="polite" className="mt-3 min-h-5 text-sm text-danger">
        {error}
      </p>

      <button
        type="submit"
        disabled={pending}
        className="mt-2 w-full rounded-lg bg-ink px-4 py-2.5 text-sm font-medium text-page transition-opacity hover:opacity-90 disabled:opacity-60"
      >
        {pending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
