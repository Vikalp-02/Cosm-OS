import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-md py-20 text-center">
      <h1 className="text-xl font-semibold">We couldn&apos;t find that leak</h1>
      <p className="mt-2 text-sm text-ink-2">
        It may have been withdrawn after newer data came in, or the link may be wrong.
      </p>
      <Link
        href="/leaks"
        className="mt-6 inline-block rounded-lg bg-ink px-4 py-2 text-sm font-medium text-page hover:opacity-90"
      >
        See all leaks
      </Link>
    </div>
  );
}
