import { Skeleton } from "@/components/ui/states";

export default function Loading() {
  return (
    <div role="status" aria-label="Loading" className="mx-auto grid max-w-[1280px] gap-4 px-8 py-6">
      <Skeleton className="h-7 w-56" /><Skeleton className="h-4 w-96" /><Skeleton className="mt-4 h-40 w-full" /><Skeleton className="h-64 w-full" />
    </div>
  );
}
