import type {
  Branch,
  Draft,
  Job,
  MergePreview,
  Preview,
  Project,
  ProjectDetail,
  Session,
  Snapshot,
  Revision,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch("/api" + path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({
      detail: "The server could not complete this request.",
    }))) as { detail?: string };
    throw new ApiError(
      response.status,
      body.detail || "The server could not complete this request.",
    );
  }
  return response.json() as Promise<T>;
}

export const api = {
  session: () => request<Session>("/session"),
  projects: () => request<Project[]>("/projects"),
  project: (id: string) => request<ProjectDetail>("/projects/" + id),
  demo: (name?: string) =>
    request<{ job_id: string }>("/projects/demo", {
      method: "POST",
      body: JSON.stringify(name ? { name } : {}),
    }),
  testConnection: (dsn: string, schema?: string) =>
    request<{
      ok: boolean;
      server_version?: string;
      schemas?: string[];
      unsupported?: unknown[];
    }>("/connections/test", {
      method: "POST",
      body: JSON.stringify({ dsn, schema: schema || undefined }),
    }),
  createProject: (name: string, dsn: string, schema: string) =>
    request<{ job_id: string }>("/projects", {
      method: "POST",
      body: JSON.stringify({ name, dsn, schema }),
    }),
  createBranch: (projectId: string, name: string) =>
    request<{ job_id: string }>("/projects/" + projectId + "/branches", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  branch: (id: string) =>
    request<{ branch: Branch; snapshot: Snapshot; draft: Draft }>(
      "/branches/" + id,
    ),
  saveDraft: (
    id: string,
    snapshot: Snapshot,
    baseRevision: string,
    version: number,
  ) =>
    request<Draft>("/branches/" + id + "/draft", {
      method: "PUT",
      body: JSON.stringify({ snapshot, base_revision: baseRevision, version }),
    }),
  discardDraft: (id: string) =>
    request<{ ok: true }>("/branches/" + id + "/draft", { method: "DELETE" }),
  preview: (id: string) =>
    request<Preview>("/branches/" + id + "/preview", { method: "POST" }),
  commit: (id: string, message: string, version: number) =>
    request<{ job_id: string }>("/branches/" + id + "/commit", {
      method: "POST",
      body: JSON.stringify({
        message,
        version,
        request_id: crypto.randomUUID(),
      }),
    }),
  mergePreview: (
    id: string,
    resolutions?: Record<string, "source" | "target">,
  ) =>
    request<MergePreview>("/branches/" + id + "/merge-preview", {
      method: "POST",
      body: JSON.stringify(
        resolutions && Object.keys(resolutions).length ? { resolutions } : {},
      ),
    }),
  merge: (
    id: string,
    data: {
      message: string;
      source_revision: string;
      target_revision: string;
      resolutions: Record<string, "source" | "target">;
    },
  ) =>
    request<{ job_id: string }>("/branches/" + id + "/merge", {
      method: "POST",
      body: JSON.stringify({ ...data, request_id: crypto.randomUUID() }),
    }),
  history: (projectId: string) =>
    request<Revision[]>("/projects/" + projectId + "/history"),
  job: (id: string) => request<Job>("/jobs/" + id),
  retry: (id: string) =>
    request<{ job_id: string }>("/jobs/" + id + "/retry", { method: "POST" }),
};
