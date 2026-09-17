import type {
  Column,
  Constraint,
  SchemaIndex,
  SchemaTable,
  Snapshot,
} from "../types";

export const emptySnapshot: Snapshot = { tables: [] };

export function clone<T>(value: T): T {
  return structuredClone(value);
}

export function columns(table: SchemaTable): Column[] {
  return table.columns ?? [];
}

export function constraints(table: SchemaTable): Constraint[] {
  return table.constraints ?? [];
}

export function indexes(table: SchemaTable): SchemaIndex[] {
  return table.indexes ?? [];
}

export function updateTable(
  snapshot: Snapshot,
  tableId: string,
  update: (table: SchemaTable) => SchemaTable,
): Snapshot {
  return {
    tables: snapshot.tables.map((table) =>
      table.id === tableId ? update(table) : table,
    ),
  };
}

export const typeSuggestions = [
  "boolean",
  "smallint",
  "integer",
  "bigint",
  "real",
  "double precision",
  "numeric(12,2)",
  "text",
  "varchar(255)",
  "uuid",
  "date",
  "timestamp with time zone",
  "timestamp without time zone",
  "jsonb",
  "bytea",
];

export function validDataType(value: string): boolean {
  const type = value.trim();
  return /^(boolean|bool|smallint|int2|integer|int|int4|bigint|int8|real|float4|double precision|float8|float|(?:numeric|decimal)(?:\(\d+(?:\s*,\s*\d+)?\))?|text|(?:varchar|character varying)(?:\(\d+\))?|uuid|date|timestamp(?:\s+(?:with|without)\s+time\s+zone)?|jsonb|bytea)$/i.test(
    type,
  );
}
