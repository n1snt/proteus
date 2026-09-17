export type Mode = "local" | "demo";
export type JobStatus =
  "queued" | "running" | "succeeded" | "failed" | "needs_attention";
export type BranchStatus = "ready" | "busy" | "merged" | "needs_attention";

export interface Session {
  workspace_id: string;
  mode: Mode;
  version: string;
}
export interface Project {
  id: string;
  name: string;
  schema_name: string;
  is_demo: boolean;
  created_at: string;
}
export interface Branch {
  id: string;
  project_id: string | null;
  name: string;
  is_main: boolean;
  head_revision: string;
  base_revision: string;
  status: BranchStatus;
  created_at: string;
}
export interface Column {
  id: string;
  name: string;
  data_type: string;
  nullable: boolean;
  default: string | null;
  identity: "always" | "by_default" | null;
}
export interface Constraint {
  id: string;
  name: string;
  kind: "primary_key" | "unique" | "foreign_key" | "check";
  columns: string[];
  reference_table?: string | null;
  reference_columns?: string[];
  on_delete?: string | null;
  on_update?: string | null;
  expression?: string | null;
}
export interface SchemaIndex {
  id: string;
  name: string;
  columns: string[];
  unique: boolean;
}
export interface SchemaTable {
  id: string;
  name: string;
  columns?: Column[];
  constraints?: Constraint[];
  indexes?: SchemaIndex[];
}
export interface Snapshot {
  tables: SchemaTable[];
}
export interface Draft {
  snapshot: Snapshot;
  base_revision: string;
  version: number;
  updated_at: string;
}
export interface JobStep {
  id?: string;
  status?: string;
  description?: string;
  detail?: string;
  sql?: string;
  [key: string]: unknown;
}
export interface Job {
  id: string;
  kind: string;
  status: JobStatus;
  stage: string;
  steps: JobStep[];
  error: string | null;
  project_id: string;
  branch_id: string | null;
  created_at: string;
  updated_at: string;
  result: Record<string, unknown> | null;
}
export interface ProjectDetail {
  project: Project;
  branches: Branch[];
  jobs: Job[];
}
export interface Change {
  id: string;
  kind: "added" | "removed" | "modified";
  object_type: string;
  table_name: string;
  object_name: string;
  summary: string;
  before: unknown;
  after: unknown;
}
export interface PlanStep {
  id: string;
  sql: string;
  description: string;
  transactional: boolean;
  impact: "metadata" | "scan" | "rewrite" | "destructive" | "index";
  operation: string;
}
export interface Preview {
  changes: Change[];
  steps: PlanStep[];
  warnings: string[];
}
export interface MergeConflict {
  id: string;
  object_type: string;
  object_name: string;
  property: string;
  reason: string;
  base: unknown;
  source: unknown;
  target: unknown;
  resolution: "source" | "target" | null;
}
export interface MergePreview extends Preview {
  base_revision: string;
  source_revision: string;
  target_revision: string;
  snapshot: Snapshot;
  conflicts: MergeConflict[];
}
export interface Revision {
  id: string;
  branch_id: string;
  message: string;
  parents: string[];
  created_at: string;
  kind: string;
}
