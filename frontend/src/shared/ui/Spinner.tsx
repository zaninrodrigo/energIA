/** Pure loading indicator. `role="status"` + `aria-live="polite"` announce the state change to
 *  assistive technology without stealing focus. */
export function Spinner() {
  return (
    <div role="status" aria-live="polite">
      Cargando…
    </div>
  );
}
