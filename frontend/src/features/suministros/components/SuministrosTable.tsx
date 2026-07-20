import { Table } from "../../../shared/ui/Table";
import type { TableColumn } from "../../../shared/ui/Table";
import type { Suministro } from "../types";

export interface SuministrosTableProps {
  items: Suministro[];
}

// `categoria_tarifaria_id` is rendered as-is: the API does not yet expose a name-resolution
// endpoint for it (nor for `cliente_id`) -- see README.md, "Deuda conocida", and
// PROJECT_MASTER_SPEC.md.
const columns: TableColumn<Suministro>[] = [
  {
    key: "numero_suministro",
    header: "Número de suministro",
    render: (row) => row.numero_suministro,
  },
  {
    key: "categoria_tarifaria_id",
    header: "Categoría",
    render: (row) => row.categoria_tarifaria_id,
  },
  { key: "localidad", header: "Localidad", render: (row) => row.localidad ?? "—" },
  { key: "estado", header: "Estado", render: (row) => row.estado },
];

/** Pure/presentational: renders whatever `items` it receives. No data-fetching, loading, error
 *  or empty-state logic here -- that belongs to the container, `SuministrosPage`. */
export function SuministrosTable({ items }: SuministrosTableProps) {
  return <Table columns={columns} rows={items} getRowKey={(row) => row.id} />;
}
