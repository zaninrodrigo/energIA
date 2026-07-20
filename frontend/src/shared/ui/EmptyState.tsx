export interface EmptyStateProps {
  message?: string;
}

const DEFAULT_MESSAGE = "No se encontraron suministros.";

/** Pure empty-result state, shown when a query succeeds but returns zero items. */
export function EmptyState({ message = DEFAULT_MESSAGE }: EmptyStateProps) {
  return <p className="rounded-lg border border-dashed border-slate-300 px-4 py-12 text-center text-sm text-slate-500">{message}</p>;
}
