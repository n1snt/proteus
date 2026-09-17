import { useState } from "react";
import { Columns3, Plus, Search, Table2, Trash2 } from "lucide-react";
import { columns, constraints, indexes, updateTable } from "../lib/schema";
import type { Column, SchemaTable, Snapshot } from "../types";
import { Empty } from "./ui";

export function ObjectBrowser({
  snapshot,
  selected,
  onSelect,
  onAdd,
  disabled,
}: {
  snapshot: Snapshot;
  selected: string | null;
  onSelect: (id: string) => void;
  onAdd: () => void;
  disabled: boolean;
}) {
  const [query, setQuery] = useState("");
  const tables = snapshot.tables.filter((table) =>
    table.name.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <section className="object-browser panel">
      <div className="panel-top">
        <span className="panel-title">Objects</span>
        <button aria-label="Create table" disabled={disabled} onClick={onAdd}>
          <Plus size={16} />
        </button>
      </div>
      <label className="search">
        <Search size={15} />
        <input
          aria-label="Find a table"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Find a table"
        />
      </label>
      <div className="object-list">
        {tables.map((table) => (
          <button
            key={table.id}
            className={
              "object-item " + (table.id === selected ? "selected" : "")
            }
            onClick={() => onSelect(table.id)}
          >
            <Table2 size={16} />
            <span>{table.name}</span>
            <small>{columns(table).length}</small>
          </button>
        ))}
        {tables.length === 0 && (
          <Empty
            icon={<Table2 size={22} />}
            title="No tables"
            text="Create a table to start a schema draft."
          />
        )}
      </div>
    </section>
  );
}

type Confirm = (title: string, text: string, action: () => void) => void;

export function StructurePanel({
  table,
  snapshot,
  readOnly,
  changeSnapshot,
  ask,
  add,
  edit,
  openConstraint,
  openIndex,
}: {
  table: SchemaTable | null;
  snapshot: Snapshot;
  readOnly: boolean;
  changeSnapshot: (snapshot: Snapshot) => void;
  ask: Confirm;
  add: () => void;
  edit: (column: Column) => void;
  openConstraint: () => void;
  openIndex: () => void;
}) {
  if (!table)
    return (
      <section className="structure panel">
        <Empty
          icon={<Columns3 size={27} />}
          title="Choose a table"
          text="Its columns, constraints, and indexes appear here."
        />
      </section>
    );
  const tableId = table.id;
  const activeTable = table;
  function removeColumn(column: Column) {
    const dependent = [
      ...constraints(activeTable)
        .filter((item) => item.columns.includes(column.id))
        .map((item) => item.name),
      ...indexes(activeTable)
        .filter((item) => item.columns.includes(column.id))
        .map((item) => item.name),
    ];
    const dependencyText = dependent.length
      ? " Dependent objects: " + dependent.join(", ") + "."
      : "";
    ask(
      "Remove column",
      "Remove " + column.name + " from the draft?" + dependencyText,
      () =>
        changeSnapshot(
          updateTable(snapshot, tableId, (current) => ({
            ...current,
            columns: columns(current).filter((item) => item.id !== column.id),
            constraints: constraints(current).filter(
              (item) => !item.columns.includes(column.id),
            ),
            indexes: indexes(current).filter(
              (item) => !item.columns.includes(column.id),
            ),
          })),
        ),
    );
  }
  function removeObject(
    id: string,
    label: string,
    key: "constraints" | "indexes",
  ) {
    ask(
      "Remove " + key.slice(0, -1),
      "Remove " + label + " from the draft?",
      () =>
        changeSnapshot(
          updateTable(snapshot, tableId, (current) => ({
            ...current,
            [key]: (current[key] || []).filter((item) => item.id !== id),
          })),
        ),
    );
  }
  return (
    <section className="structure panel">
      <header className="structure-head">
        <div>
          <p className="eyebrow">Table</p>
          <h2>{table.name}</h2>
        </div>
        {!readOnly && (
          <button
            className="button-quiet danger"
            onClick={() =>
              ask(
                "Drop table",
                "Drop " +
                  table.name +
                  " from the draft? This is destructive when the draft is applied.",
                () =>
                  changeSnapshot({
                    tables: snapshot.tables.filter(
                      (item) => item.id !== tableId,
                    ),
                  }),
              )
            }
          >
            <Trash2 size={15} />
            Drop
          </button>
        )}
      </header>
      <DefinitionGroup
        title="Columns"
        count={columns(table).length}
        action={readOnly ? undefined : add}
      >
        {columns(table).map((column) => (
          <div className="definition-row" key={column.id}>
            <span className="definition-main">
              <strong>{column.name}</strong>
              <code>{column.data_type}</code>
            </span>
            <span className="chips">
              {column.identity && <span className="chip">identity</span>}
              {!column.nullable && <span className="chip">not null</span>}
              {column.default && <span className="chip">default</span>}
            </span>
            {!readOnly && (
              <span className="row-actions">
                <button
                  aria-label={"Edit " + column.name}
                  onClick={() => edit(column)}
                >
                  Edit
                </button>
                <button
                  aria-label={"Remove " + column.name}
                  onClick={() => removeColumn(column)}
                >
                  <Trash2 size={14} />
                </button>
              </span>
            )}
          </div>
        ))}
      </DefinitionGroup>
      <DefinitionGroup
        title="Constraints"
        count={constraints(table).length}
        action={readOnly ? undefined : openConstraint}
      >
        {constraints(table).map((constraint) => (
          <div className="definition-row" key={constraint.id}>
            <span className="definition-main">
              <strong>{constraint.name}</strong>
              <code>{constraint.kind.replace("_", " ")}</code>
            </span>
            <span className="muted">
              {constraint.columns
                .map(
                  (id) =>
                    columns(table).find((column) => column.id === id)?.name ||
                    "removed",
                )
                .join(", ")}
            </span>
            {!readOnly && (
              <button
                className="icon-only"
                aria-label={"Remove " + constraint.name}
                onClick={() =>
                  removeObject(
                    constraint.id,
                    "constraint " + constraint.name,
                    "constraints",
                  )
                }
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        ))}
      </DefinitionGroup>
      <DefinitionGroup
        title="Indexes"
        count={indexes(table).length}
        action={readOnly ? undefined : openIndex}
      >
        {indexes(table).map((index) => (
          <div className="definition-row" key={index.id}>
            <span className="definition-main">
              <strong>{index.name}</strong>
              <code>{index.unique ? "unique index" : "index"}</code>
            </span>
            <span className="muted">
              {index.columns
                .map(
                  (id) =>
                    columns(table).find((column) => column.id === id)?.name ||
                    "removed",
                )
                .join(", ")}
            </span>
            {!readOnly && (
              <button
                className="icon-only"
                aria-label={"Remove " + index.name}
                onClick={() =>
                  removeObject(index.id, "index " + index.name, "indexes")
                }
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        ))}
      </DefinitionGroup>
    </section>
  );
}

function DefinitionGroup({
  title,
  count,
  action,
  children,
}: {
  title: string;
  count: number;
  action?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="definition-group">
      <div className="group-head">
        <h3>
          {title}
          <span>{count}</span>
        </h3>
        {action && (
          <button className="text-action" onClick={action}>
            <Plus size={14} />
            Add
          </button>
        )}
      </div>
      {count ? (
        children
      ) : (
        <p className="empty-line">No {title.toLowerCase()}.</p>
      )}
    </section>
  );
}
