import { type FormEvent, useState } from "react";
import { Trash2 } from "lucide-react";
import { api } from "../api";
import { message, staleMessage } from "../lib/format";
import { typeSuggestions, validDataType } from "../lib/schema";
import type {
  Column,
  Constraint,
  SchemaIndex,
  SchemaTable,
  Snapshot,
} from "../types";
import { Field, Modal, Notice } from "./ui";

export function ConfirmDialog({
  title,
  text,
  close,
  confirm,
}: {
  title: string;
  text: string;
  close: () => void;
  confirm: () => void;
}) {
  return (
    <Modal title={title} close={close}>
      <div className="form-stack">
        <Notice kind="warning">{text}</Notice>
        <p className="form-note">
          This only changes the draft. Review the generated database plan before
          applying it.
        </p>
        <div className="modal-actions">
          <button type="button" className="button-quiet" onClick={close}>
            Cancel
          </button>
          <button className="destructive-button" onClick={confirm}>
            <Trash2 size={15} />
            Confirm change
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function BranchDialog({
  projectId,
  close,
}: {
  projectId: string;
  close: () => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      window.location.assign(
        "/jobs/" + (await api.createBranch(projectId, name)).job_id,
      );
    } catch (reason) {
      setError(message(reason));
      setBusy(false);
    }
  }

  return (
    <Modal title="Create branch" close={close}>
      <form className="form-stack" onSubmit={submit}>
        <p className="form-note">
          Creates an isolated database with this schema. Existing rows are not
          copied.
        </p>
        <Field label="Branch name">
          <input
            autoFocus
            required
            pattern="[a-zA-Z0-9_-]+"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="add-invoice-status"
          />
        </Field>
        {error && <Notice kind="error">{error}</Notice>}
        <div className="modal-actions">
          <button type="button" className="button-quiet" onClick={close}>
            Cancel
          </button>
          <button disabled={busy}>{busy ? "Creating" : "Create branch"}</button>
        </div>
      </form>
    </Modal>
  );
}

export function TableDialog({
  close,
  submit,
}: {
  close: () => void;
  submit: (table: SchemaTable) => void;
}) {
  const [name, setName] = useState("");
  return (
    <Modal title="Create table" close={close}>
      <form
        className="form-stack"
        onSubmit={(event) => {
          event.preventDefault();
          submit({
            id: crypto.randomUUID(),
            name,
            columns: [],
            constraints: [],
            indexes: [],
          });
        }}
      >
        <Field label="Table name" hint="Use a PostgreSQL identifier.">
          <input
            autoFocus
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="invoice_events"
          />
        </Field>
        <div className="modal-actions">
          <button type="button" className="button-quiet" onClick={close}>
            Cancel
          </button>
          <button>Create table</button>
        </div>
      </form>
    </Modal>
  );
}

export function ColumnDialog({
  table,
  existing,
  close,
  submit,
}: {
  table: SchemaTable;
  existing: Column | null;
  close: () => void;
  submit: (column: Column) => void;
}) {
  const [form, setForm] = useState({
    name: existing?.name || "",
    dataType: existing?.data_type || "text",
    nullable: existing?.nullable ?? true,
    defaultValue: existing?.default || "",
    identity: existing?.identity || "",
  });
  const [typeError, setTypeError] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!existing && !validDataType(form.dataType)) {
      setTypeError("Choose a supported PostgreSQL scalar type.");
      return;
    }
    submit({
      id: existing?.id || crypto.randomUUID(),
      name: form.name,
      data_type: form.dataType.trim(),
      nullable: form.nullable,
      default: form.defaultValue || null,
      identity:
        form.identity === "always" || form.identity === "by_default"
          ? form.identity
          : null,
    });
  }

  return (
    <Modal
      title={(existing ? "Edit column in " : "Add column to ") + table.name}
      close={close}
    >
      <form className="form-stack" onSubmit={handleSubmit}>
        <Field label="Column name">
          <input
            autoFocus
            required
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="status"
          />
        </Field>
        <Field
          label="Data type"
          hint={
            existing
              ? "Existing type is preserved. Supported changes are checked when previewing."
              : "Use a listed type or a numeric or varchar type with parameters."
          }
        >
          <input
            required
            list="postgres-types"
            value={form.dataType}
            onChange={(event) => {
              setForm({ ...form, dataType: event.target.value });
              setTypeError("");
            }}
            placeholder="numeric(12,2)"
          />
          <datalist id="postgres-types">
            {typeSuggestions.map((type) => (
              <option key={type} value={type} />
            ))}
          </datalist>
        </Field>
        {typeError && <Notice kind="error">{typeError}</Notice>}
        <div className="form-inline">
          <label className="check">
            <input
              type="checkbox"
              checked={form.nullable}
              onChange={(event) =>
                setForm({ ...form, nullable: event.target.checked })
              }
            />
            Nullable
          </label>
          <Field label="Identity">
            <select
              value={form.identity}
              onChange={(event) =>
                setForm({ ...form, identity: event.target.value })
              }
            >
              <option value="">None</option>
              <option value="always">Always</option>
              <option value="by_default">By default</option>
            </select>
          </Field>
        </div>
        <Field
          label="Default expression"
          hint="Supported SQL expression, or leave blank."
        >
          <input
            value={form.defaultValue}
            onChange={(event) =>
              setForm({ ...form, defaultValue: event.target.value })
            }
            placeholder="'draft'"
          />
        </Field>
        <div className="modal-actions">
          <button type="button" className="button-quiet" onClick={close}>
            Cancel
          </button>
          <button>{existing ? "Save column" : "Add column"}</button>
        </div>
      </form>
    </Modal>
  );
}

export function ConstraintDialog({
  table,
  snapshot,
  close,
  submit,
}: {
  table: SchemaTable;
  snapshot: Snapshot;
  close: () => void;
  submit: (constraint: Constraint) => void;
}) {
  const [form, setForm] = useState({
    name: "",
    kind: "unique",
    selected: [] as string[],
    referenceTable: "",
    referenceColumns: [] as string[],
    expression: "",
    onDelete: "",
    onUpdate: "",
  });
  const reference = snapshot.tables.find(
    (item) => item.id === form.referenceTable,
  );
  const selectedColumns =
    table.columns?.filter((column) => form.selected.includes(column.id)) || [];
  function toggle(id: string, key: "selected" | "referenceColumns") {
    setForm({
      ...form,
      [key]: form[key].includes(id)
        ? form[key].filter((item) => item !== id)
        : [...form[key], id],
    });
  }

  return (
    <Modal title={"Add constraint to " + table.name} close={close}>
      <form
        className="form-stack"
        onSubmit={(event) => {
          event.preventDefault();
          submit({
            id: crypto.randomUUID(),
            name: form.name,
            kind: form.kind as Constraint["kind"],
            columns: form.selected,
            reference_table:
              form.kind === "foreign_key" ? form.referenceTable : null,
            reference_columns:
              form.kind === "foreign_key" ? form.referenceColumns : [],
            expression: form.kind === "check" ? form.expression : null,
            on_delete:
              form.kind === "foreign_key" ? form.onDelete || null : null,
            on_update:
              form.kind === "foreign_key" ? form.onUpdate || null : null,
          });
        }}
      >
        <Field label="Constraint name">
          <input
            autoFocus
            required
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </Field>
        <Field label="Kind">
          <select
            value={form.kind}
            onChange={(event) =>
              setForm({
                ...form,
                kind: event.target.value,
                selected: [],
                referenceColumns: [],
              })
            }
          >
            <option value="primary_key">Primary key</option>
            <option value="unique">Unique</option>
            <option value="foreign_key">Foreign key</option>
            <option value="check">Check</option>
          </select>
        </Field>
        {form.kind !== "check" && (
          <fieldset className="column-picker">
            <legend>Columns</legend>
            {table.columns?.map((column) => (
              <label className="check" key={column.id}>
                <input
                  type="checkbox"
                  checked={form.selected.includes(column.id)}
                  onChange={() => toggle(column.id, "selected")}
                />
                {column.name}
              </label>
            ))}
          </fieldset>
        )}
        {form.kind === "foreign_key" && (
          <>
            <Field label="Referenced table">
              <select
                required
                value={form.referenceTable}
                onChange={(event) =>
                  setForm({
                    ...form,
                    referenceTable: event.target.value,
                    referenceColumns: [],
                  })
                }
              >
                <option value="">Select a table</option>
                {snapshot.tables
                  .filter((item) => item.id !== table.id)
                  .map((item) => (
                    <option value={item.id} key={item.id}>
                      {item.name}
                    </option>
                  ))}
              </select>
            </Field>
            <fieldset className="column-picker">
              <legend>Referenced columns in matching order</legend>
              {reference?.columns?.map((column) => (
                <label className="check" key={column.id}>
                  <input
                    type="checkbox"
                    checked={form.referenceColumns.includes(column.id)}
                    onChange={() => toggle(column.id, "referenceColumns")}
                  />
                  {column.name}
                </label>
              ))}
            </fieldset>
            <div className="form-inline">
              <Field label="On delete">
                <select
                  value={form.onDelete}
                  onChange={(event) =>
                    setForm({ ...form, onDelete: event.target.value })
                  }
                >
                  <option value="">No action</option>
                  <option>RESTRICT</option>
                  <option>CASCADE</option>
                  <option>SET NULL</option>
                  <option>SET DEFAULT</option>
                </select>
              </Field>
              <Field label="On update">
                <select
                  value={form.onUpdate}
                  onChange={(event) =>
                    setForm({ ...form, onUpdate: event.target.value })
                  }
                >
                  <option value="">No action</option>
                  <option>RESTRICT</option>
                  <option>CASCADE</option>
                  <option>SET NULL</option>
                  <option>SET DEFAULT</option>
                </select>
              </Field>
            </div>
          </>
        )}
        {form.kind === "check" && (
          <Field label="Check expression">
            <input
              required
              value={form.expression}
              onChange={(event) =>
                setForm({ ...form, expression: event.target.value })
              }
              placeholder="amount >= 0"
            />
          </Field>
        )}
        {form.kind === "foreign_key" &&
          form.selected.length !== form.referenceColumns.length && (
            <Notice kind="warning">
              Choose the same number of local and referenced columns.
            </Notice>
          )}
        <div className="modal-actions">
          <button type="button" className="button-quiet" onClick={close}>
            Cancel
          </button>
          <button
            disabled={
              (form.kind !== "check" && !form.selected.length) ||
              (form.kind === "foreign_key" &&
                form.selected.length !== form.referenceColumns.length)
            }
          >
            {selectedColumns.length ? "Add constraint" : "Add constraint"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function IndexDialog({
  table,
  close,
  submit,
}: {
  table: SchemaTable;
  close: () => void;
  submit: (index: SchemaIndex) => void;
}) {
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [unique, setUnique] = useState(false);
  function toggle(id: string) {
    setSelected(
      selected.includes(id)
        ? selected.filter((item) => item !== id)
        : [...selected, id],
    );
  }
  return (
    <Modal title={"Add index to " + table.name} close={close}>
      <form
        className="form-stack"
        onSubmit={(event) => {
          event.preventDefault();
          submit({ id: crypto.randomUUID(), name, columns: selected, unique });
        }}
      >
        <Field label="Index name">
          <input
            autoFocus
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={table.name + "_status_idx"}
          />
        </Field>
        <fieldset className="column-picker">
          <legend>Columns in order</legend>
          {table.columns?.map((column) => (
            <label className="check" key={column.id}>
              <input
                type="checkbox"
                checked={selected.includes(column.id)}
                onChange={() => toggle(column.id)}
              />
              {column.name}
            </label>
          ))}
        </fieldset>
        <label className="check">
          <input
            type="checkbox"
            checked={unique}
            onChange={(event) => setUnique(event.target.checked)}
          />
          Unique index
        </label>
        <div className="modal-actions">
          <button type="button" className="button-quiet" onClick={close}>
            Cancel
          </button>
          <button disabled={!selected.length}>Add index</button>
        </div>
      </form>
    </Modal>
  );
}

export function CommitDialog({
  kind,
  close,
  submit,
}: {
  kind: "commit" | "merge";
  close: () => void;
  submit: (message: string) => Promise<void>;
}) {
  const [commitMessage, setCommitMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function handle(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await submit(commitMessage);
    } catch (reason) {
      setError(staleMessage(reason));
      setBusy(false);
    }
  }
  return (
    <Modal
      title={kind === "merge" ? "Apply merge" : "Apply and commit"}
      close={close}
    >
      <form className="form-stack" onSubmit={handle}>
        <p className="form-note">
          {kind === "merge"
            ? "This applies the resolved schema to main after a final target revision check."
            : "This applies the reviewed draft to the isolated branch database and creates a revision."}
        </p>
        <Field label="Commit message">
          <input
            autoFocus
            required
            value={commitMessage}
            onChange={(event) => setCommitMessage(event.target.value)}
            placeholder={
              kind === "merge"
                ? "Merge invoice status branch"
                : "Add invoice status"
            }
          />
        </Field>
        {error && <Notice kind="error">{error}</Notice>}
        <div className="modal-actions">
          <button className="button-quiet" type="button" onClick={close}>
            Cancel
          </button>
          <button disabled={busy}>
            {busy
              ? "Submitting"
              : kind === "merge"
                ? "Apply merge"
                : "Apply and commit"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
