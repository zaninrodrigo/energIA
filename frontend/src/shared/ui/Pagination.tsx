export interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onPrevious: () => void;
  onNext: () => void;
}

/** Pure prev/next pagination control. Owns no state: the container decides what `offset` means
 *  and how it changes (see `SuministrosPage`). */
export function Pagination({ total, limit, offset, onPrevious, onNext }: PaginationProps) {
  const hasPrevious = offset > 0;
  const hasNext = offset + limit < total;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  const rangeLabel = total === 0 ? `Mostrando 0 de 0` : `Mostrando ${from}–${to} de ${total}`;

  return (
    <nav aria-label="Paginación de suministros">
      <button type="button" onClick={onPrevious} disabled={!hasPrevious}>
        Anterior
      </button>
      <span>{rangeLabel}</span>
      <button type="button" onClick={onNext} disabled={!hasNext}>
        Siguiente
      </button>
    </nav>
  );
}
