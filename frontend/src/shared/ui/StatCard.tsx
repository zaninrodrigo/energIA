import type { ReactNode } from "react";
import type { RiskTone } from "./Badge";

export type StatCardTone = RiskTone | "neutral";

export interface StatCardProps {
  label: string;
  value: ReactNode;
  tone?: StatCardTone;
  icon?: ReactNode;
  /** Optional one-line clarification shown under the value (e.g. spelling out an acronym in plain
   *  language for operators who don't know it). */
  hint?: string;
}

// Left-accent border per tone, reusing the same risk-scale `-fg` tokens Badge uses for text --
// one color scale, two roles (text vs. accent border), never a second palette to keep in sync.
const ACCENT_CLASSES: Record<StatCardTone, string> = {
  neutral: "border-l-slate-300",
  "very-low": "border-l-risk-very-low-fg",
  low: "border-l-risk-low-fg",
  medium: "border-l-risk-medium-fg",
  high: "border-l-risk-high-fg",
  critical: "border-l-risk-critical-fg",
};

/** KPI tile: a label, a value, an optional icon, and an optional risk-tone accent border (used
 *  by the Ranking de Riesgo dashboard's summary row -- Phase 2 -- to color-code per-nivel
 *  counts using the same scale as `Badge`). */
export function StatCard({ label, value, tone = "neutral", icon, hint }: StatCardProps) {
  return (
    <div
      data-tone={tone}
      className={`flex items-center gap-3 rounded-lg border border-slate-200 border-l-4 bg-white p-4 shadow-sm ${ACCENT_CLASSES[tone]}`}
    >
      {icon ? <div className="shrink-0 text-slate-400">{icon}</div> : null}
      <div>
        <p className="text-sm text-slate-500">{label}</p>
        <p className="text-2xl font-semibold tabular-nums text-slate-900">{value}</p>
        {hint ? <p className="mt-0.5 text-xs text-slate-400">{hint}</p> : null}
      </div>
    </div>
  );
}
