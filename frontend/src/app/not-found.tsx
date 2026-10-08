import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto grid max-w-md gap-3 px-6 py-24">
      <h1 className="text-section font-semibold text-strong">We couldn&apos;t find that page</h1>
      <p className="text-body text-muted">It may have moved, or you may not have access to it in this organization.</p>
      <Link className="text-small underline underline-offset-4" href="/">Go to the start page</Link>
    </div>
  );
}
