/** Pure loading indicator. `role="status"` + `aria-live="polite"` announce the state change to
 *  assistive technology without stealing focus. */
export function Spinner() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center justify-center gap-2 py-12 text-sm text-slate-500"
    >
      <span
        aria-hidden="true"
        className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand"
      />
      Cargando…
    </div>
  );
}
