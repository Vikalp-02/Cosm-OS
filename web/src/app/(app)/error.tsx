"use client";

export default function AppError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-auto max-w-md py-20 text-center">
      <h1 className="text-xl font-semibold">We couldn&apos;t load this page</h1>
      <p className="mt-2 text-sm text-ink-2">
        Something went wrong on our side. Your data is safe. Try again, and if it keeps
        happening, let us know.
      </p>
      <button
        type="button"
        onClick={reset}
        className="mt-6 rounded-lg bg-ink px-4 py-2 text-sm font-medium text-page hover:opacity-90"
      >
        Try again
      </button>
    </div>
  );
}
