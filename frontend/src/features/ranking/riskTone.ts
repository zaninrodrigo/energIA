import type { RiskTone } from "../../shared/ui/Badge";
import type { Clasificacion, NivelIre, Severidad } from "./types";

/**
 * Pure mapping functions from this feature's 3 backend enums onto the shared 5-step `RiskTone`
 * scale (`shared/ui/Badge.tsx`) -- one color scale, never a second one to keep in sync (see that
 * module's docstring).
 */

const NIVEL_TONE: Record<NivelIre, RiskTone> = {
  "Muy Bajo": "very-low",
  Bajo: "low",
  Medio: "medium",
  Alto: "high",
  Crítico: "critical",
};

/** `ire_nivel`'s 5 bands map 1:1 onto the 5-step scale. */
export function nivelToTone(nivel: NivelIre): RiskTone {
  return NIVEL_TONE[nivel];
}

// DEC-015's 4 bands (0-20 Normal / 21-40 Atención / 41-70 Alto Riesgo / 71-100 Crítico) reuse a
// subset of the 5-step scale: "Normal" spans what would be "very-low" + "low" combined, so it
// reads as the calmest step rather than a false "medium".
const CLASIFICACION_TONE: Record<Clasificacion, RiskTone> = {
  Normal: "very-low",
  Atención: "medium",
  "Alto Riesgo": "high",
  Crítico: "critical",
};

export function clasificacionToTone(clasificacion: Clasificacion): RiskTone {
  return CLASIFICACION_TONE[clasificacion];
}

// `ck_anomalias_severidad`'s 4 bands map onto the top 4 steps of the scale, skipping "very-low" --
// an anomaly, by definition, is never "very low risk" the way a calm suministro's ire_nivel can be.
const SEVERIDAD_TONE: Record<Severidad, RiskTone> = {
  Baja: "low",
  Media: "medium",
  Alta: "high",
  Crítica: "critical",
};

export function severidadToTone(severidad: Severidad): RiskTone {
  return SEVERIDAD_TONE[severidad];
}
