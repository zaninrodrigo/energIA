import type { ReactNode } from "react";

/**
 * The five-step risk scale (calm green -> red) shared by every place a risk signal appears:
 * `ire_nivel` maps onto it directly (5 values); `clasificacion` and `anomalias.severidad` (4
 * values each) reuse a subset of it via small mapping functions owned by the feature that knows
 * those exact backend enums (`features/ranking`, Phase 2) -- colors live in exactly one place.
 */
export type RiskTone = "very-low" | "low" | "medium" | "high" | "critical";

export interface BadgeProps {
  tone: RiskTone;
  children: ReactNode;
}

// Literal class strings (not string interpolation) so Tailwind's content scanner can see them.
const TONE_CLASSES: Record<RiskTone, string> = {
  "very-low": "bg-risk-very-low text-risk-very-low-fg",
  low: "bg-risk-low text-risk-low-fg",
  medium: "bg-risk-medium text-risk-medium-fg",
  high: "bg-risk-high text-risk-high-fg",
  critical: "bg-risk-critical text-risk-critical-fg",
};

/** Risk-colored label. Purely presentational: `tone` picks the color pair from the shared risk
 *  scale (see `src/styles/index.css`), `children` is the visible text -- this component has no
 *  knowledge of `ire_nivel`/`clasificacion`/`severidad` strings. */
export function Badge({ tone, children }: BadgeProps) {
  return (
    <span
      data-tone={tone}
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  );
}
