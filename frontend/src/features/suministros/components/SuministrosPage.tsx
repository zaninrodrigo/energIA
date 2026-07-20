import { useState } from "react";
import { EmptyState } from "../../../shared/ui/EmptyState";
import { ErrorState } from "../../../shared/ui/ErrorState";
import { Pagination } from "../../../shared/ui/Pagination";
import { Spinner } from "../../../shared/ui/Spinner";
import { useSuministros } from "../hooks";
import { SuministrosTable } from "./SuministrosTable";

const PAGE_LIMIT = 50;

/**
 * Container: owns query + pagination state (plain component state, not URL search params --
 * simplest option for Sprint 0; revisit if deep-linking/bookmarking a specific page becomes a
 * real need). `SuministrosTable` stays pure/presentational, receiving only the resolved items.
 */
export function SuministrosPage() {
  const [offset, setOffset] = useState(0);
  const query = useSuministros({ limit: PAGE_LIMIT, offset });

  if (query.isPending) {
    return <Spinner />;
  }

  if (query.isError) {
    return <ErrorState />;
  }

  const { data } = query;

  return (
    <section>
      {data.items.length === 0 ? <EmptyState /> : <SuministrosTable items={data.items} />}
      <Pagination
        total={data.total}
        limit={data.limit}
        offset={data.offset}
        onPrevious={() => setOffset((current) => Math.max(0, current - PAGE_LIMIT))}
        onNext={() => setOffset((current) => current + PAGE_LIMIT)}
      />
    </section>
  );
}
