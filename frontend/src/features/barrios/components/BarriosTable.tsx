import { Badge } from "../../../shared/ui/Badge";
import { Table } from "../../../shared/ui/Table";
import type { TableColumn } from "../../../shared/ui/Table";
import { nivelToTone } from "../../ranking/riskTone";
import type { BarrioRiesgo } from "../types";

export interface BarriosTableProps {
  barrios: BarrioRiesgo[];
}

const columns: TableColumn<BarrioRiesgo>[] = [
  { key: "barrio", header: "Barrio", render: (b) => b.barrio ?? "Sin barrio" },
  {
    key: "total_medidores",
    header: "Medidores",
    render: (b) => <span className="tabular-nums">{b.total_medidores}</span>,
  },
  {
    key: "potencial",
    header: "Potencial (IRE máx.)",
    render: (b) => (
      <span className="flex items-center gap-2">
        <span className="tabular-nums font-semibold text-slate-900">{b.ire_maximo}</span>
        <Badge tone={nivelToTone(b.nivel)}>{b.nivel}</Badge>
      </span>
    ),
  },
  {
    key: "ire_promedio",
    header: "IRE promedio",
    render: (b) => <span className="tabular-nums">{b.ire_promedio}</span>,
  },
  {
    key: "con_anomalias",
    header: "Con anomalías",
    render: (b) => <span className="tabular-nums">{b.con_anomalias}</span>,
  },
];

/** Pure/presentational: renders the barrios in the order given (the backend orders them worst-IRE
 *  first, so the barrios to inspect are at the top). */
export function BarriosTable({ barrios }: BarriosTableProps) {
  return (
    <Table
      columns={columns}
      rows={barrios}
      getRowKey={(b) => `${b.localidad ?? ""}/${b.barrio ?? ""}`}
    />
  );
}
