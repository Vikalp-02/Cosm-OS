import "server-only";

import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/**
 * Fetch from the API as the signed-in user, from a server component.
 *
 * A missing or expired session sends the visitor to the sign-in page, and a
 * missing record renders the nearest not-found page, so pages only ever handle
 * the data they asked for.
 */
export async function api<T>(path: string): Promise<T> {
  const jar = await cookies();
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      headers: { cookie: jar.toString() },
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new ApiError(503, "The service did not respond.");
  }
  if (response.status === 401) redirect("/login");
  if (response.status === 404) notFound();
  if (!response.ok) throw new ApiError(response.status, `Request failed (${response.status}).`);
  return (await response.json()) as T;
}

/** Whether the visitor has a live session. Never redirects. */
export async function isSignedIn(): Promise<boolean> {
  const jar = await cookies();
  if (jar.getAll().length === 0) return false;
  try {
    const response = await fetch(`${API_URL}/api/auth/me`, {
      headers: { cookie: jar.toString() },
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    return response.ok;
  } catch {
    return false;
  }
}
