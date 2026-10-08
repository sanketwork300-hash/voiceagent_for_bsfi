"use client";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "@/utils/cn";
import { EmptyState, ErrorState, SkeletonRows } from "./states";

export interface Column<T> {
  key: string;
  header: string;
  cell: (row: T) => React.ReactNode;
  sort?: (row: T) => string | number;
  className?: string;
  /** shown as the card title on small screens */
  primary?: boolean;
  hideOnMobile?: boolean;
}

/**
 * Dense operational table. Below 768px rows become stacked cards instead of a squeezed table.
 * Large lists are paginated client-side; server pagination plugs into the same props.
 */
export function DataTable<T>({ rows, columns, getKey, onRowClick, loading, error, onRetry, empty, pageSize = 25, caption }: {
  rows: T[] | undefined; columns: Column<T>[]; getKey: (r: T) => string; onRowClick?: (r: T) => void;
  loading?: boolean; error?: unknown; onRetry?: () => void; empty: { title: string; description: string; action?: React.ReactNode };
  pageSize?: number; caption: string;
}) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const [page, setPage] = useState(0);
  const sorted = useMemo(() => {
    if (!rows || !sort) return rows ?? [];
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sort) return rows;
    return [...rows].sort((a, b) => (col.sort!(a) > col.sort!(b) ? 1 : col.sort!(a) < col.sort!(b) ? -1 : 0) * sort.dir);
  }, [rows, sort, columns]);
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (loading || !rows) return <SkeletonRows label={`Loading ${caption}`} />;
  if (!rows.length) return <EmptyState {...empty} />;
  const pages = Math.ceil(sorted.length / pageSize);
  const visible = sorted.slice(page * pageSize, page * pageSize + pageSize);
  const primary = columns.find((c) => c.primary) ?? columns[0];

  return (
    <div>
      <div className="hidden overflow-x-auto rounded-panel border border-border md:block">
        <table className="w-full text-small">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr className="border-b border-border bg-surface text-left">
              {columns.map((c) => (
                <th key={c.key} scope="col" className={cn("px-3 py-2 font-medium text-muted", c.className)}
                    aria-sort={sort?.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
                  {c.sort ? (
                    <button className="inline-flex items-center gap-1 hover:text-foreground" onClick={() => setSort((s) => ({ key: c.key, dir: s?.key === c.key && s.dir === 1 ? -1 : 1 }))}>
                      {c.header}
                      {sort?.key === c.key && (sort.dir === 1 ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />)}
                    </button>
                  ) : c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr key={getKey(r)} onClick={onRowClick ? () => onRowClick(r) : undefined}
                  onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(r); } : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  className={cn("border-b border-border last:border-0", onRowClick && "cursor-pointer hover:bg-hover focus-visible:bg-hover")}>
                {columns.map((c) => <td key={c.key} className={cn("px-3 py-2.5 align-middle", c.className)}>{c.cell(r)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="grid gap-2 md:hidden" aria-label={caption}>
        {visible.map((r) => (
          <li key={getKey(r)}>
            <div role={onRowClick ? "button" : undefined} tabIndex={onRowClick ? 0 : undefined}
                 onClick={onRowClick ? () => onRowClick(r) : undefined}
                 onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(r); } : undefined}
                 className="grid gap-2 rounded-panel border border-border bg-surface p-3">
              <div className="text-body font-medium text-foreground">{primary.cell(r)}</div>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-small">
                {columns.filter((c) => c !== primary && !c.hideOnMobile).map((c) => (
                  <div key={c.key} className="min-w-0"><dt className="text-meta text-muted">{c.header}</dt><dd className="truncate">{c.cell(r)}</dd></div>
                ))}
              </dl>
            </div>
          </li>
        ))}
      </ul>
      {pages > 1 && (
        <div className="mt-3 flex items-center justify-between text-small text-muted">
          <span>{page * pageSize + 1}–{Math.min(sorted.length, (page + 1) * pageSize)} of {sorted.length}</span>
          <div className="flex gap-2">
            <button className="rounded-control border border-border-strong px-2 py-1 disabled:opacity-40" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
            <button className="rounded-control border border-border-strong px-2 py-1 disabled:opacity-40" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        </div>
      )}
    </div>
  );
}
