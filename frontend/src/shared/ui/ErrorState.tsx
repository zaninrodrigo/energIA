export interface ErrorStateProps {
  message?: string;
}

const DEFAULT_MESSAGE = "No se pudieron cargar los suministros. Vuelva a intentarlo.";

/** Pure error state. `role="alert"` announces it immediately to assistive technology. Reuses
 *  the risk scale's "critical" tone -- an error and a critical risk are the same semantic
 *  status (something urgent, requiring attention), so this is one color decision, not two. */
export function ErrorState({ message = DEFAULT_MESSAGE }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-risk-critical-fg/20 bg-risk-critical px-4 py-3 text-sm text-risk-critical-fg"
    >
      {message}
    </div>
  );
}
