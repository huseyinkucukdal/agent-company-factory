"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col items-center justify-center gap-4 bg-bg text-fg">
        <h1 className="text-2xl font-semibold">An error occurred</h1>
        <p className="max-w-md text-center text-muted">{error.message}</p>
        <button
          onClick={() => reset()}
          className="rounded-md border border-border px-4 py-2 text-sm hover:bg-card"
        >
          Try again
        </button>
      </body>
    </html>
  );
}
