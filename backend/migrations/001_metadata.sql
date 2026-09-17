CREATE TABLE IF NOT EXISTS workspaces (
    id UUID PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('local', 'demo')),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    is_demo BOOLEAN NOT NULL DEFAULT false,
    encrypted_dsn BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS environments (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    database_name TEXT,
    encrypted_dsn BYTEA NOT NULL,
    is_sandbox BOOLEAN NOT NULL DEFAULT false,
    provision_job_id UUID,
    provision_status TEXT NOT NULL DEFAULT 'ready' CHECK (provision_status IN ('pending', 'ready', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, database_name)
);

CREATE TABLE IF NOT EXISTS revisions (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    branch_id UUID,
    message TEXT NOT NULL,
    parents JSONB NOT NULL DEFAULT '[]'::jsonb,
    snapshot JSONB NOT NULL,
    checksum TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('baseline', 'commit', 'merge')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'committed', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS branches (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    environment_id UUID NOT NULL REFERENCES environments(id),
    name TEXT NOT NULL,
    is_main BOOLEAN NOT NULL DEFAULT false,
    head_revision UUID REFERENCES revisions(id),
    base_revision UUID REFERENCES revisions(id),
    status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'busy', 'merged', 'needs_attention')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'revisions_branch_id_fkey'
    ) THEN
        ALTER TABLE revisions
            ADD CONSTRAINT revisions_branch_id_fkey
            FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS drafts (
    branch_id UUID PRIMARY KEY REFERENCES branches(id) ON DELETE CASCADE,
    snapshot JSONB NOT NULL,
    base_revision UUID NOT NULL REFERENCES revisions(id),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plans (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    branch_id UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    source_revision UUID REFERENCES revisions(id),
    target_revision UUID REFERENCES revisions(id),
    candidate_revision UUID NOT NULL REFERENCES revisions(id),
    plan JSONB NOT NULL,
    checksum TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    branch_id UUID REFERENCES branches(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('import', 'demo', 'branch', 'commit', 'merge')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'needs_attention')),
    stage TEXT NOT NULL DEFAULT 'queued',
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    error TEXT,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    request_id TEXT,
    plan_id UUID REFERENCES plans(id),
    worker_id UUID,
    claimed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, request_id)
);

CREATE INDEX IF NOT EXISTS jobs_claim_idx ON jobs (status, created_at) WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS projects_workspace_idx ON projects (workspace_id, created_at);
CREATE INDEX IF NOT EXISTS branches_project_idx ON branches (project_id, created_at);
CREATE INDEX IF NOT EXISTS revisions_project_idx ON revisions (project_id, created_at DESC);
