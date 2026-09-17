import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Clock3,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { displayTime, message, short } from "../lib/format";
import type { Job } from "../types";
import { Mark, Notice, Status } from "../components/ui";

export function JobPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [workspaceBranchId, setWorkspaceBranchId] = useState<string | null>(
    null,
  );
  const active = job?.status === "queued" || job?.status === "running";

  useEffect(() => {
    let stopped = false;
    async function load() {
      try {
        const result = await api.job(jobId);
        if (!stopped) setJob(result);
      } catch (reason) {
        if (!stopped) setError(message(reason));
      }
    }
    void load();
    const timer = window.setInterval(load, 2500);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [jobId]);

  useEffect(() => {
    const resultProjectId =
      typeof job?.result?.project_id === "string"
        ? job.result.project_id
        : null;
    const projectId = job?.project_id || resultProjectId;
    if (
      !job ||
      !projectId ||
      job.branch_id ||
      typeof job.result?.branch_id === "string"
    )
      return;
    api
      .project(projectId)
      .then((detail) => {
        setWorkspaceBranchId(
          detail.branches.find((branch) => branch.is_main)?.id ||
            detail.branches[0]?.id ||
            null,
        );
      })
      .catch(() => setWorkspaceBranchId(null));
  }, [job]);

  async function retry() {
    try {
      navigate("/jobs/" + (await api.retry(jobId)).job_id);
    } catch (reason) {
      setError(message(reason));
    }
  }
  if (!job && !error)
    return (
      <main className="center-state">
        <LoaderCircle className="spin" />
        <p>Opening job</p>
      </main>
    );
  if (error)
    return (
      <main className="center-state">
        <AlertTriangle />
        <h1>Job unavailable</h1>
        <p>{error}</p>
        <Link to="/">Back to projects</Link>
      </main>
    );
  const resultBranchId =
    typeof job?.result?.branch_id === "string" ? job.result.branch_id : null;
  const resultProjectId =
    typeof job?.result?.project_id === "string" ? job.result.project_id : null;
  const projectId = job?.project_id || resultProjectId;
  const branchId = resultBranchId || job?.branch_id || workspaceBranchId;
  const branchUrl =
    branchId && projectId
      ? "/projects/" + projectId + "/branches/" + branchId
      : "/";
  return (
    <main className="job-page">
      <header className="job-header">
        <Link className="brand" to="/">
          <Mark />
          <span>proteus</span>
        </Link>
        <Link className="button-quiet" to={branchUrl}>
          <ArrowLeft size={15} />
          Workspace
        </Link>
      </header>
      <section className="job-card">
        <div className="job-top">
          <div>
            <p className="eyebrow">Durable execution</p>
            <h1>{job?.kind.replaceAll("_", " ")}</h1>
            <p className="job-id">
              Job <code>{short(job?.id)}</code> started{" "}
              {job && displayTime(job.created_at)}
            </p>
          </div>
          {job && <Status status={job.status} />}
        </div>
        {active && (
          <div className="job-progress">
            <span className="progress-line">
              <i />
            </span>
            <p>
              <LoaderCircle className="spin" size={16} />
              {job?.stage || "Waiting for a worker"}
            </p>
          </div>
        )}
        <JobSteps job={job} />
        {job?.status === "succeeded" && (
          <Notice kind="success">This job completed successfully.</Notice>
        )}
        {job?.status === "failed" && (
          <Notice kind="error">
            <strong>Execution failed.</strong>{" "}
            {job.error || "Review the job details and draft before retrying."}
          </Notice>
        )}
        {job?.status === "needs_attention" && (
          <Notice kind="warning">
            <strong>Manual attention is required.</strong>{" "}
            {job.error || "Review the recorded steps before retrying."}
          </Notice>
        )}
        <div className="job-actions">
          {(job?.status === "failed" || job?.status === "needs_attention") && (
            <button onClick={retry}>
              <RefreshCw size={15} />
              Retry recovery
            </button>
          )}
          <Link className="button-quiet" to={branchUrl}>
            Return to workspace
          </Link>
        </div>
      </section>
    </main>
  );
}

function JobSteps({ job }: { job: Job | null }) {
  if (!job?.steps?.length)
    return (
      <div className="job-steps">
        <p className="empty-line">No execution steps have been recorded yet.</p>
      </div>
    );
  return (
    <div className="job-steps">
      {job.steps.map((step, index) => {
        const state = String(step.state || step.status || "pending");
        const completed =
          state === "complete" || state === "succeeded" || state === "applied";
        return (
          <div className="job-step" key={step.id || index}>
            <span>
              {completed ? <Check size={15} /> : <Clock3 size={15} />}
            </span>
            <div>
              <strong>
                {String(
                  step.description ||
                    step.operation ||
                    step.id ||
                    "Execution step",
                )}
              </strong>
              <p>{state}</p>
              {step.detail && <p>{String(step.detail)}</p>}
              {step.sql && <code>{String(step.sql)}</code>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
