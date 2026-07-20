export interface EmptyStateProps {
  message?: string;
}

const DEFAULT_MESSAGE = "No se encontraron suministros.";

/** Pure empty-result state, shown when a query succeeds but returns zero items. */
export function EmptyState({ message = DEFAULT_MESSAGE }: EmptyStateProps) {
  return <p>{message}</p>;
}
