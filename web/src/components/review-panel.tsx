import {
  Check,
  Clock3,
  Code2,
  FileClock,
  LoaderCircle,
  LockKeyhole,
  Merge,
} from "lucide-react";
import { displayTime, formatValue, short } from "../lib/format";
import type {
  Branch,
  Change,
  MergePreview,
  PlanStep,
  Revision,
} from "../types";
import { Empty, Notice } from "./ui";

type Preview = { changes: Change[]; steps: PlanStep[]; warnings: string[] };

export function ReviewPanel({
  tab,
  setTab,
  dirty,
  saving,
  preview,
  mergePreview,
  history,
  branch,
  onSave,
  onDiscard,
  onPreview,
  onCommit,
  onCompare,
  onResolve,
  onMerge,
}: {
  tab: "draft" | "history" | "merge";
  setTab: (tab: "draft" | "history" | "merge") => void;
  dirty: boolean;
  saving: boolean;
  preview: Preview | null;
  mergePreview: MergePreview | null;
  history: Revision[];
  branch: Branch;
  onSave: () => void;
  onDiscard: () => void;
  onPreview: () => void;
  onCommit: () => void;
  onCompare: () => void;
  onResolve: (id: string, resolution: "source" | "target") => void;
  onMerge: () => void;
}) {
  const readOnly = branch.is_main || branch.status !== "ready";
  return (
    <section className="review panel">
      <nav className="review-tabs">
        <button
          className={tab === "draft" ? "active" : ""}
          onClick={() => setTab("draft")}
        >
          Draft{dirty && <span className="dirty-dot" />}
        </button>
        {!branch.is_main && (
          <button
            className={tab === "merge" ? "active" : ""}
            onClick={() => setTab("merge")}
          >
            Merge
          </button>
        )}
        <button
          className={tab === "history" ? "active" : ""}
          onClick={() => setTab("history")}
        >
          History
        </button>
      </nav>
      {tab === "draft" && (
        <DraftReview
          dirty={dirty}
          saving={saving}
          preview={preview}
          readOnly={readOnly}
          branch={branch}
          onSave={onSave}
          onDiscard={onDiscard}
          onPreview={onPreview}
          onCommit={onCommit}
        />
      )}
      {tab === "history" && <HistoryList history={history} />}
      {tab === "merge" && (
        <div className="review-scroll">
          {branch.status === "merged" ? (
            <Empty
              icon={<Check size={25} />}
              title="Branch merged"
              text="Create a new branch from main for further work."
            />
          ) : mergePreview ? (
            <MergeReview
              preview={mergePreview}
              onResolve={onResolve}
              onMerge={onMerge}
            />
          ) : (
            <CompareEmpty onCompare={onCompare} />
          )}
        </div>
      )}
    </section>
  );
}

function DraftReview({
  dirty,
  saving,
  preview,
  readOnly,
  branch,
  onSave,
  onDiscard,
  onPreview,
  onCommit,
}: {
  dirty: boolean;
  saving: boolean;
  preview: Preview | null;
  readOnly: boolean;
  branch: Branch;
  onSave: () => void;
  onDiscard: () => void;
  onPreview: () => void;
  onCommit: () => void;
}) {
  return (
    <div className="review-scroll">
      <div className="save-state">
        <span className={dirty ? "unsaved" : "saved"}>
          {saving ? (
            <LoaderCircle className="spin" size={14} />
          ) : dirty ? (
            <Clock3 size={14} />
          ) : (
            <Check size={14} />
          )}
          {saving ? "Saving draft" : dirty ? "Unsaved edits" : "Saved draft"}
        </span>
        {dirty && !readOnly && (
          <button className="text-action" onClick={onSave}>
            Save now
          </button>
        )}
      </div>
      {readOnly ? (
        <Empty
          icon={<LockKeyhole size={23} />}
          title="No editable draft"
          text={
            branch.is_main
              ? "Main changes begin on a feature branch."
              : "This branch cannot accept edits right now."
          }
        />
      ) : (
        <>
          <div className="review-heading">
            <div>
              <p className="eyebrow">Change review</p>
              <h3>
                {preview
                  ? preview.changes.length + " planned changes"
                  : "Review your draft"}
              </h3>
            </div>
            <button className="button-quiet" onClick={onPreview}>
              <Code2 size={15} />
              Preview
            </button>
          </div>
          {preview ? (
            <Plan preview={preview} />
          ) : (
            <p className="empty-line review-empty">
              Save your structured edits, then inspect their generated plan
              before committing.
            </p>
          )}
          <div className="review-actions">
            <button className="button-quiet danger" onClick={onDiscard}>
              Discard draft
            </button>
            <button disabled={!preview} onClick={onCommit}>
              <Check size={15} />
              Apply and commit
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function CompareEmpty({ onCompare }: { onCompare: () => void }) {
  return (
    <>
      <div className="review-heading">
        <div>
          <p className="eyebrow">Feature branch to main</p>
          <h3>Compare pinned revisions</h3>
        </div>
      </div>
      <p className="empty-line review-empty">
        Build a three-way comparison against the latest main revision. SQL is
        generated from the resolved result.
      </p>
      <button onClick={onCompare}>
        <Merge size={15} />
        Compare to main
      </button>
    </>
  );
}

export function Plan({ preview }: { preview: Preview }) {
  return (
    <>
      <div className="change-list">
        {preview.changes.map((change) => (
          <div className="change" key={change.id}>
            <span className={"change-kind " + change.kind}>{change.kind}</span>
            <div>
              <strong>{change.object_name}</strong>
              <p>{change.summary}</p>
            </div>
          </div>
        ))}
      </div>
      {preview.warnings.map((warning) => (
        <Notice kind="warning" key={warning}>
          {warning}
        </Notice>
      ))}
      <div className="plan-title">
        <span>SQL plan</span>
        <small>{preview.steps.length} steps</small>
      </div>
      <div className="sql-steps">
        {preview.steps.map((step) => (
          <SqlStep key={step.id} step={step} />
        ))}
      </div>
    </>
  );
}

function SqlStep({ step }: { step: PlanStep }) {
  return (
    <details className="sql-step">
      <summary>
        <span className={"impact " + step.impact}>{step.impact}</span>
        <span>{step.description}</span>
        <small>{step.transactional ? "transactional" : "separate phase"}</small>
      </summary>
      <pre>{step.sql}</pre>
    </details>
  );
}

function MergeReview({
  preview,
  onResolve,
  onMerge,
}: {
  preview: MergePreview;
  onResolve: (id: string, resolution: "source" | "target") => void;
  onMerge: () => void;
}) {
  const open = preview.conflicts.filter((conflict) => !conflict.resolution);
  return (
    <>
      <div className="merge-summary">
        <span>
          <strong>{preview.changes.length}</strong> changes
        </span>
        <span className={open.length ? "conflict-count" : "resolved-count"}>
          <strong>{open.length}</strong>{" "}
          {open.length === 1 ? "conflict" : "conflicts"}
        </span>
      </div>
      <p className="revision-line">
        Source <code>{short(preview.source_revision)}</code> into main{" "}
        <code>{short(preview.target_revision)}</code>
      </p>
      {preview.conflicts.map((conflict) => (
        <article className="conflict" key={conflict.id}>
          <div>
            <span className="conflict-label">Conflict</span>
            <strong>
              {conflict.object_name} · {conflict.property}
            </strong>
            <p>{conflict.reason}</p>
          </div>
          <div className="conflict-values">
            <Value label="Base" value={conflict.base} />
            <button
              className={
                conflict.resolution === "source" ? "choice selected" : "choice"
              }
              onClick={() => onResolve(conflict.id, "source")}
            >
              <span>Use source</span>
              <code>{formatValue(conflict.source)}</code>
            </button>
            <button
              className={
                conflict.resolution === "target" ? "choice selected" : "choice"
              }
              onClick={() => onResolve(conflict.id, "target")}
            >
              <span>Use main</span>
              <code>{formatValue(conflict.target)}</code>
            </button>
          </div>
        </article>
      ))}
      <Plan preview={preview} />
      <div className="review-actions">
        <span className="merge-ready">
          {open.length
            ? "Resolve every conflict to continue."
            : "All conflicts resolved."}
        </span>
        <button disabled={open.length > 0} onClick={onMerge}>
          <Merge size={15} />
          Apply merge
        </button>
      </div>
    </>
  );
}

function Value({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="value">
      <span>{label}</span>
      <code>{formatValue(value)}</code>
    </div>
  );
}

function HistoryList({ history }: { history: Revision[] }) {
  return (
    <div className="review-scroll">
      <div className="history-head">
        <p className="eyebrow">Revisions</p>
        <h3>Schema history</h3>
      </div>
      {history.length ? (
        <ol className="history-list">
          {history.map((revision) => (
            <li key={revision.id}>
              <span className="history-node" />
              <div>
                <strong>{revision.message}</strong>
                <p>
                  <code>{short(revision.id)}</code> · {revision.kind} ·{" "}
                  {displayTime(revision.created_at)}
                </p>
                {revision.parents.length > 1 && (
                  <small>
                    Merge parents: {revision.parents.map(short).join(", ")}
                  </small>
                )}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <Empty
          icon={<FileClock size={23} />}
          title="No revisions yet"
          text="Applied schema changes appear here."
        />
      )}
    </div>
  );
}
