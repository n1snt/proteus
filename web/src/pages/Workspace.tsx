import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  Database,
  GitBranch,
  LockKeyhole,
  Merge,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import {
  BranchDialog,
  ColumnDialog,
  CommitDialog,
  ConfirmDialog,
  ConstraintDialog,
  IndexDialog,
  TableDialog,
} from "../components/dialogs";
import { ObjectBrowser, StructurePanel } from "../components/schema-panel";
import { ReviewPanel } from "../components/review-panel";
import { Mark, Notice, Status } from "../components/ui";
import { message, staleMessage } from "../lib/format";
import { clone, columns, emptySnapshot, updateTable } from "../lib/schema";
import type {
  Branch,
  Column,
  Draft,
  MergePreview,
  ProjectDetail,
  Revision,
  Session,
  Snapshot,
} from "../types";

type Dialog =
  | "branch"
  | "table"
  | "column"
  | "constraint"
  | "index"
  | "commit"
  | "merge"
  | null;
type Confirmation = { title: string; text: string; action: () => void };

export function Workspace({ session }: { session: Session }) {
  const { projectId = "", branchId = "" } = useParams();
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [branch, setBranch] = useState<Branch | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [local, setLocal] = useState<Snapshot>(emptySnapshot);
  const [dirty, setDirty] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<"draft" | "history" | "merge">("draft");
  const [preview, setPreview] = useState<Awaited<
    ReturnType<typeof api.preview>
  > | null>(null);
  const [mergePreview, setMergePreview] = useState<MergePreview | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [editingColumn, setEditingColumn] = useState<Column | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [history, setHistory] = useState<Revision[]>([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const dirtyRef = useRef(false);
  const localRef = useRef<Snapshot>(emptySnapshot);
  const draftRef = useRef<Draft | null>(null);
  const generation = useRef(0);

  function replaceLocal(snapshot: Snapshot, isDirty: boolean) {
    localRef.current = snapshot;
    dirtyRef.current = isDirty;
    setLocal(snapshot);
    setDirty(isDirty);
  }

  async function load(
    initial = false,
    expectedGeneration = generation.current,
  ) {
    try {
      const [projectResult, branchResult] = await Promise.all([
        api.project(projectId),
        api.branch(branchId),
      ]);
      if (expectedGeneration !== generation.current) return;
      setDetail(projectResult);
      setBranch(branchResult.branch);
      // A poll must not replace draft metadata while a local edit is pending.
      if (initial || !dirtyRef.current) {
        draftRef.current = branchResult.draft;
        setDraft(branchResult.draft);
        replaceLocal(clone(branchResult.draft.snapshot), false);
        setSelected((current) =>
          current &&
          branchResult.draft.snapshot.tables.some(
            (table) => table.id === current,
          )
            ? current
            : branchResult.draft.snapshot.tables[0]?.id || null,
        );
      }
    } catch (reason) {
      if (expectedGeneration === generation.current) setError(message(reason));
    }
  }

  useEffect(() => {
    generation.current += 1;
    const currentGeneration = generation.current;
    setDetail(null);
    setBranch(null);
    setDraft(null);
    setError("");
    setPreview(null);
    setMergePreview(null);
    setHistory([]);
    setTab("draft");
    setDialog(null);
    setEditingColumn(null);
    setConfirmation(null);
    replaceLocal(emptySnapshot, false);
    void load(true, currentGeneration);
  }, [projectId, branchId]);

  useEffect(() => {
    const currentGeneration = generation.current;
    const timer = window.setInterval(() => {
      void load(false, currentGeneration);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [projectId, branchId]);

  useEffect(() => {
    if (tab === "history")
      api
        .history(projectId)
        .then(setHistory)
        .catch((reason) => setError(message(reason)));
  }, [tab, projectId]);
  useEffect(() => {
    function beforeUnload(event: BeforeUnloadEvent) {
      if (dirtyRef.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);

  const selectedTable =
    local.tables.find((table) => table.id === selected) || null;
  const readOnly =
    !branch ||
    branch.is_main ||
    branch.status === "merged" ||
    branch.status === "busy" ||
    branch.status === "needs_attention";
  function changeSnapshot(next: Snapshot) {
    replaceLocal(next, true);
    setPreview(null);
    setMergePreview(null);
  }
  function navigateWithDraftWarning(event: React.MouseEvent) {
    if (
      dirtyRef.current &&
      !window.confirm("You have unsaved draft edits. Leave this branch?")
    )
      event.preventDefault();
  }

  async function save(): Promise<Draft | null> {
    const currentDraft = draftRef.current;
    if (!currentDraft || !branch || readOnly) return currentDraft;
    const snapshotToSave = clone(localRef.current);
    setSaving(true);
    setError("");
    try {
      const saved = await api.saveDraft(
        branch.id,
        snapshotToSave,
        currentDraft.base_revision,
        currentDraft.version,
      );
      draftRef.current = saved;
      setDraft(saved);
      if (JSON.stringify(localRef.current) === JSON.stringify(snapshotToSave))
        replaceLocal(clone(saved.snapshot), false);
      return saved;
    } catch (reason) {
      setError(staleMessage(reason));
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function discard() {
    if (!branch) return;
    try {
      await api.discardDraft(branch.id);
      await load(true);
      setPreview(null);
    } catch (reason) {
      setError(message(reason));
    }
  }
  async function makePreview() {
    const saved = dirtyRef.current ? await save() : draftRef.current;
    if (!saved || !branch) return;
    try {
      setPreview(await api.preview(branch.id));
      setTab("draft");
    } catch (reason) {
      setError(message(reason));
    }
  }
  async function compare(resolutions?: Record<string, "source" | "target">) {
    if (!branch) return;
    try {
      setMergePreview(await api.mergePreview(branch.id, resolutions));
      setTab("merge");
    } catch (reason) {
      setError(staleMessage(reason));
    }
  }

  if (!detail || !branch || !draft)
    return (
      <main className="center-state">
        <RefreshCw className="spin" />
        <p>Loading branch</p>
        {error && <Notice kind="error">{error}</Notice>}
      </main>
    );
  return (
    <main className="app-shell">
      <aside className="sidebar">
        <Link className="brand" to="/" onClick={navigateWithDraftWarning}>
          <Mark />
          <span>proteus</span>
        </Link>
        <div className="side-project">
          <span className="side-label">Project</span>
          <strong>{detail.project.name}</strong>
          <small>
            <Database size={13} /> {detail.project.schema_name}
          </small>
        </div>
        <nav className="branch-nav" aria-label="Branches">
          <div className="side-heading">
            <span>Branches</span>
            <button
              aria-label="Create branch"
              onClick={() => setDialog("branch")}
            >
              <Plus size={15} />
            </button>
          </div>
          {detail.branches.map((item) => (
            <Link
              key={item.id}
              className={
                "branch-link " + (item.id === branch.id ? "active" : "")
              }
              to={"/projects/" + projectId + "/branches/" + item.id}
              onClick={navigateWithDraftWarning}
            >
              <GitBranch size={15} />
              <span>{item.name}</span>
              {item.is_main && <small>main</small>}
              <Status status={item.status} />
            </Link>
          ))}
        </nav>
        <div className="side-bottom">
          <Link
            to="/"
            className="side-action"
            onClick={navigateWithDraftWarning}
          >
            <ArrowLeft size={15} />
            All projects
          </Link>
          <span className="mode-label">
            <LockKeyhole size={13} />
            {session.mode === "demo" ? "Demo workspace" : "Private workspace"}
          </span>
        </div>
      </aside>
      <section className="work-area">
        <header className="workspace-header">
          <div className="crumb">
            <span>{detail.project.name}</span>
            <ChevronDown size={15} />
            <strong>
              <GitBranch size={15} />
              {branch.name}
            </strong>
            <Status status={branch.status} />
          </div>
          <div className="header-actions">
            {!branch.is_main && branch.status === "ready" && (
              <button className="button-quiet" onClick={() => void compare()}>
                <Merge size={15} />
                Compare to main
              </button>
            )}
            <button className="button-quiet" onClick={() => void load(false)}>
              <RefreshCw size={15} />
              Refresh
            </button>
          </div>
        </header>
        {error && (
          <div className="workspace-notice">
            <Notice kind="error">
              {error}
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={15} />
              </button>
            </Notice>
          </div>
        )}
        {branch.is_main && (
          <div className="read-only-note">
            <LockKeyhole size={15} />
            <span>
              <strong>Main is read-only.</strong> Create a branch to prepare a
              reviewed schema change.
            </span>
            <button onClick={() => setDialog("branch")}>Create branch</button>
          </div>
        )}
        {branch.status === "merged" && (
          <div className="read-only-note">
            <Check size={15} />
            <span>
              <strong>This branch has merged.</strong> Its final schema remains
              available for review.
            </span>
            <button onClick={() => setDialog("branch")}>
              Branch from main
            </button>
          </div>
        )}
        <div className="work-grid">
          <ObjectBrowser
            snapshot={local}
            selected={selected}
            onSelect={setSelected}
            onAdd={() => setDialog("table")}
            disabled={readOnly}
          />
          <StructurePanel
            table={selectedTable}
            snapshot={local}
            readOnly={readOnly}
            changeSnapshot={changeSnapshot}
            ask={(title, text, action) =>
              setConfirmation({ title, text, action })
            }
            add={() => {
              setEditingColumn(null);
              setDialog("column");
            }}
            edit={(column) => {
              setEditingColumn(column);
              setDialog("column");
            }}
            openConstraint={() => setDialog("constraint")}
            openIndex={() => setDialog("index")}
          />
          <ReviewPanel
            tab={tab}
            setTab={setTab}
            dirty={dirty}
            saving={saving}
            preview={preview}
            mergePreview={mergePreview}
            history={history}
            branch={branch}
            onSave={() => void save()}
            onDiscard={() =>
              setConfirmation({
                title: "Discard draft",
                text: "Discard all saved edits on this branch? The draft will return to the committed branch schema.",
                action: () => {
                  void discard();
                },
              })
            }
            onPreview={() => void makePreview()}
            onCommit={() => setDialog("commit")}
            onCompare={() => void compare()}
            onResolve={(id, resolution) => {
              const next = Object.fromEntries(
                (mergePreview?.conflicts || [])
                  .filter((conflict) => conflict.resolution)
                  .map((conflict) => [
                    conflict.id,
                    conflict.resolution as "source" | "target",
                  ]),
              );
              next[id] = resolution;
              void compare(next);
            }}
            onMerge={() => setDialog("merge")}
          />
        </div>
      </section>
      {dialog === "branch" && (
        <BranchDialog projectId={projectId} close={() => setDialog(null)} />
      )}
      {dialog === "table" && (
        <TableDialog
          close={() => setDialog(null)}
          submit={(table) => {
            changeSnapshot({ tables: [...localRef.current.tables, table] });
            setSelected(table.id);
            setDialog(null);
          }}
        />
      )}
      {dialog === "column" && selectedTable && (
        <ColumnDialog
          table={selectedTable}
          existing={editingColumn}
          close={() => {
            setEditingColumn(null);
            setDialog(null);
          }}
          submit={(column) => {
            changeSnapshot(
              updateTable(localRef.current, selectedTable.id, (table) => ({
                ...table,
                columns: editingColumn
                  ? columns(table).map((item) =>
                      item.id === column.id ? column : item,
                    )
                  : [...columns(table), column],
              })),
            );
            setEditingColumn(null);
            setDialog(null);
          }}
        />
      )}
      {dialog === "constraint" && selectedTable && (
        <ConstraintDialog
          table={selectedTable}
          snapshot={localRef.current}
          close={() => setDialog(null)}
          submit={(constraint) => {
            changeSnapshot(
              updateTable(localRef.current, selectedTable.id, (table) => ({
                ...table,
                constraints: [...(table.constraints || []), constraint],
              })),
            );
            setDialog(null);
          }}
        />
      )}
      {dialog === "index" && selectedTable && (
        <IndexDialog
          table={selectedTable}
          close={() => setDialog(null)}
          submit={(index) => {
            changeSnapshot(
              updateTable(localRef.current, selectedTable.id, (table) => ({
                ...table,
                indexes: [...(table.indexes || []), index],
              })),
            );
            setDialog(null);
          }}
        />
      )}
      {dialog === "commit" && (
        <CommitDialog
          kind="commit"
          close={() => setDialog(null)}
          submit={async (commitMessage) => {
            const saved = dirtyRef.current ? await save() : draftRef.current;
            if (!saved) return;
            window.location.assign(
              "/jobs/" +
                (await api.commit(branch.id, commitMessage, saved.version))
                  .job_id,
            );
          }}
        />
      )}
      {dialog === "merge" && mergePreview && (
        <CommitDialog
          kind="merge"
          close={() => setDialog(null)}
          submit={async (commitMessage) => {
            const resolutions = Object.fromEntries(
              mergePreview.conflicts
                .filter((conflict) => conflict.resolution)
                .map((conflict) => [
                  conflict.id,
                  conflict.resolution as "source" | "target",
                ]),
            );
            window.location.assign(
              "/jobs/" +
                (
                  await api.merge(branch.id, {
                    message: commitMessage,
                    source_revision: mergePreview.source_revision,
                    target_revision: mergePreview.target_revision,
                    resolutions,
                  })
                ).job_id,
            );
          }}
        />
      )}
      {confirmation && (
        <ConfirmDialog
          title={confirmation.title}
          text={confirmation.text}
          close={() => setConfirmation(null)}
          confirm={() => {
            confirmation.action();
            setConfirmation(null);
          }}
        />
      )}
    </main>
  );
}
