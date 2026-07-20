import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Table } from "./Table";

interface Row {
  id: string;
  name: string;
}

describe("Table", () => {
  const rows: Row[] = [
    { id: "1", name: "Alpha" },
    { id: "2", name: "Beta" },
  ];
  const columns = [{ key: "name", header: "Nombre", render: (row: Row) => row.name }];

  it("renders one header cell per column", () => {
    render(<Table columns={columns} rows={rows} getRowKey={(row) => row.id} />);

    expect(screen.getByRole("columnheader", { name: "Nombre" })).toBeInTheDocument();
  });

  it("renders one row per item using the column's render function", () => {
    render(<Table columns={columns} rows={rows} getRowKey={(row) => row.id} />);

    expect(screen.getAllByRole("row")).toHaveLength(rows.length + 1); // + header row
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
  });
});
