import { Badge } from "../../../shared/ui/Badge";
import { Button } from "../../../shared/ui/Button";
import { Table } from "../../../shared/ui/Table";
import type { TableColumn } from "../../../shared/ui/Table";
import { clasificacionToTone, nivelToTone } from "../riskTone";
import type { ResultadoRankingItem } from "../types";

export interface RankingTableProps {
  items: ResultadoRankingItem[];
  onSelect: (item: ResultadoRankingItem) => void;
}

const columns: TableColumn<ResultadoRankingItem>[] = [
  {
    key: "numero_suministro",
    header: "Ruta-folio (suministro)",
    render: (row) => row.numero_suministro,
  },
  {
    key: "medidor",
    header: "Medidor",
    render: (row) => <span className="tabular-nums">{row.medidor ?? "—"}</span>,
  },
  { key: "localidad", header: "Localidad", render: (row) => row.localidad ?? "—" },
  {
    key: "categoria_tarifaria",
    header: "Categoría tarifaria",
    render: (row) => row.categoria_tarifaria,
  },
  {
    key: "ire",
    header: "IRE",
    render: (row) => (
      <span className="flex items-center gap-2">
        <span className="tabular-nums font-semibold text-slate-900">{row.ire_valor}</span>
        <Badge tone={nivelToTone(row.ire_nivel)}>{row.ire_nivel}</Badge>
      </span>
    ),
  },
  {
    key: "clasificacion",
    header: "Clasificación",
    render: (row) => <Badge tone={clasificacionToTone(row.clasificacion)}>{row.clasificacion}</Badge>,
  },
  {
    key: "anomalias",
    header: "Anomalías",
    render: (row) => <span className="tabular-nums">{row.anomalias.length}</span>,
  },
  {
    key: "iee_kwh",
    header: "IEE (kWh)",
    render: (row) => <span className="tabular-nums">{row.iee_kwh?.toFixed(2) ?? "—"}</span>,
  },
];

/** Pure/presentational: renders whatever `items` it receives, ordered exactly as given (the
 *  container/backend own `ire_valor` descending, RN-009). Row order and IRE-descending sorting
 *  are never re-derived here. */
export function RankingTable({ items, onSelect }: RankingTableProps) {
  return (
    <Table
      columns={[
        ...columns,
        {
          key: "acciones",
          header: "",
          render: (row) => (
            <Button variant="secondary" size="sm" onClick={() => onSelect(row)}>
              Ver detalle
            </Button>
          ),
        },
      ]}
      rows={items}
      getRowKey={(row) => row.suministro_id}
    />
  );
}
