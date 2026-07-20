export interface ErrorStateProps {
  message?: string;
}

const DEFAULT_MESSAGE = "No se pudieron cargar los suministros. Vuelva a intentarlo.";

/** Pure error state. `role="alert"` announces it immediately to assistive technology. */
export function ErrorState({ message = DEFAULT_MESSAGE }: ErrorStateProps) {
  return <div role="alert">{message}</div>;
}
