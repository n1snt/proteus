import { type FormEvent, useEffect, useState } from "react";
import { ArrowRight, Database, LockKeyhole, Settings2 } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { message } from "../lib/format";
import type { Project, ProjectDetail, Session } from "../types";
import { Field, Mark, Modal, Notice } from "../components/ui";

export function Landing({ session }: { session: Session }) {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ name: "", dsn: "", schema: "public" });
  const [test, setTest] = useState<{
    ok: boolean;
    server_version?: string;
    unsupported?: unknown[];
  } | null>(null);

  useEffect(() => {
    api
      .projects()
      .then(setProjects)
      .catch((reason) => setError(message(reason)));
  }, []);
  async function startSample() {
    setBusy(true);
    setError("");
    try {
      navigate("/jobs/" + (await api.demo()).job_id);
    } catch (reason) {
      setError(message(reason));
      setBusy(false);
    }
  }
  async function testConnection() {
    setBusy(true);
    setError("");
    try {
      setTest(await api.testConnection(form.dsn, form.schema));
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }
  async function connect(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      navigate(
        "/jobs/" +
          (await api.createProject(form.name, form.dsn, form.schema)).job_id,
      );
    } catch (reason) {
      setError(message(reason));
      setBusy(false);
    }
  }

  return (
    <main className="landing">
      <header className="landing-header">
        <Link to="/" className="brand">
          <Mark />
          <span>proteus</span>
        </Link>
        <span className="mode-label">
          <LockKeyhole size={14} />
          {session.mode === "demo" ? "Isolated demo" : "Self-hosted"}
        </span>
      </header>
      <section className="landing-hero">
        <p className="eyebrow">PostgreSQL schema workbench</p>
        <h1>
          Change structure.
          <br />
          <em>Keep your footing.</em>
        </h1>
        <p className="hero-copy">
          Create a schema-only branch, review the exact database work, then
          merge a verified change into main.
        </p>
        <div className="start-grid">
          <button
            className="path-card sample-card"
            onClick={startSample}
            disabled={busy}
          >
            <span className="path-icon">
              <Database size={22} />
            </span>
            <strong>Try sample database</strong>
            <small>Customers, invoices, payments</small>
            <ArrowRight size={18} />
          </button>
          <button
            className="path-card"
            onClick={() => setConnectionOpen(true)}
            disabled={session.mode === "demo"}
          >
            <span className="path-icon">
              <Settings2 size={22} />
            </span>
            <strong>Connect PostgreSQL</strong>
            <small>
              {session.mode === "demo"
                ? "Available in self-hosted mode"
                : "Import a tracked schema"}
            </small>
            <ArrowRight size={18} />
          </button>
        </div>
        {error && <Notice kind="error">{error}</Notice>}
      </section>
      {projects.length > 0 && (
        <section className="recent-projects">
          <div>
            <p className="eyebrow">Your workspace</p>
            <h2>Recent projects</h2>
          </div>
          <div className="project-list">
            {projects.map((project) => (
              <ProjectRow project={project} key={project.id} />
            ))}
          </div>
        </section>
      )}
      <footer className="landing-foot">
        Proteus tracks schema only. Branches copy structure, not rows.
      </footer>
      {connectionOpen && (
        <ConnectionDialog
          form={form}
          setForm={(next) => {
            setForm(next);
            setTest(null);
          }}
          test={test}
          error={error}
          busy={busy}
          close={() => setConnectionOpen(false)}
          testConnection={testConnection}
          connect={connect}
        />
      )}
    </main>
  );
}

function ProjectRow({ project }: { project: Project }) {
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  useEffect(() => {
    api
      .project(project.id)
      .then(setDetail)
      .catch(() => undefined);
  }, [project.id]);
  const branch =
    detail?.branches.find((item) => item.is_main) || detail?.branches[0];
  return (
    <Link
      className="project-row"
      to={branch ? "/projects/" + project.id + "/branches/" + branch.id : "/"}
    >
      <span className="project-symbol">
        <Database size={17} />
      </span>
      <span>
        <strong>{project.name}</strong>
        <small>
          {project.schema_name} schema {project.is_demo ? "· sample" : ""}
        </small>
      </span>
      <ArrowRight size={17} />
    </Link>
  );
}

function ConnectionDialog({
  form,
  setForm,
  test,
  error,
  busy,
  close,
  testConnection,
  connect,
}: {
  form: { name: string; dsn: string; schema: string };
  setForm: (form: { name: string; dsn: string; schema: string }) => void;
  test: {
    ok: boolean;
    server_version?: string;
    unsupported?: unknown[];
  } | null;
  error: string;
  busy: boolean;
  close: () => void;
  testConnection: () => void;
  connect: (event: FormEvent) => void;
}) {
  const [mode, setMode] = useState<"url" | "fields">("url");
  const [connection, setConnection] = useState({
    host: "host.docker.internal",
    port: "5432",
    database: "postgres",
    user: "postgres",
    password: "",
    sslmode: "prefer",
  });
  function connectionUrl(value: typeof connection) {
    const host =
      value.host.includes(":") && !value.host.startsWith("[")
        ? "[" + value.host + "]"
        : value.host;
    return (
      "postgresql://" +
      encodeURIComponent(value.user) +
      ":" +
      encodeURIComponent(value.password) +
      "@" +
      host +
      ":" +
      value.port +
      "/" +
      encodeURIComponent(value.database) +
      "?sslmode=" +
      value.sslmode
    );
  }
  function updateConnection(next: typeof connection) {
    setConnection(next);
    setForm({ ...form, dsn: connectionUrl(next) });
  }
  return (
    <Modal title="Connect PostgreSQL" close={close}>
      <form className="form-stack" onSubmit={connect}>
        <p className="form-note">
          The application container cannot reach your host through{" "}
          <code>localhost</code>. Use a Compose service name,{" "}
          <code>host.docker.internal</code>, or a reachable remote host.
        </p>
        <Field label="Project name">
          <input
            required
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="Billing production"
          />
        </Field>
        <div className="form-inline">
          <button
            type="button"
            className={mode === "url" ? "" : "button-quiet"}
            onClick={() => setMode("url")}
          >
            Connection URL
          </button>
          <button
            type="button"
            className={mode === "fields" ? "" : "button-quiet"}
            onClick={() => {
              setMode("fields");
              updateConnection(connection);
            }}
          >
            Connection fields
          </button>
        </div>
        {mode === "url" ? (
          <Field label="PostgreSQL URL">
            <input
              required
              type="password"
              value={form.dsn}
              onChange={(event) =>
                setForm({ ...form, dsn: event.target.value })
              }
              placeholder="postgresql://user:password@host:5432/database"
            />
          </Field>
        ) : (
          <>
            <div className="form-inline">
              <Field label="Host">
                <input
                  required
                  value={connection.host}
                  onChange={(event) =>
                    updateConnection({
                      ...connection,
                      host: event.target.value,
                    })
                  }
                />
              </Field>
              <Field label="Port">
                <input
                  required
                  type="number"
                  min="1"
                  max="65535"
                  value={connection.port}
                  onChange={(event) =>
                    updateConnection({
                      ...connection,
                      port: event.target.value,
                    })
                  }
                />
              </Field>
            </div>
            <Field label="Database">
              <input
                required
                value={connection.database}
                onChange={(event) =>
                  updateConnection({
                    ...connection,
                    database: event.target.value,
                  })
                }
              />
            </Field>
            <div className="form-inline">
              <Field label="User">
                <input
                  required
                  autoComplete="username"
                  value={connection.user}
                  onChange={(event) =>
                    updateConnection({
                      ...connection,
                      user: event.target.value,
                    })
                  }
                />
              </Field>
              <Field label="Password">
                <input
                  type="password"
                  autoComplete="current-password"
                  value={connection.password}
                  onChange={(event) =>
                    updateConnection({
                      ...connection,
                      password: event.target.value,
                    })
                  }
                />
              </Field>
            </div>
            <Field label="TLS mode">
              <select
                value={connection.sslmode}
                onChange={(event) =>
                  updateConnection({
                    ...connection,
                    sslmode: event.target.value,
                  })
                }
              >
                <option value="prefer">Prefer TLS</option>
                <option value="require">Require TLS</option>
                <option value="verify-full">Verify certificate and host</option>
                <option value="disable">Disable TLS (local only)</option>
              </select>
            </Field>
          </>
        )}
        <Field label="Schema">
          <input
            required
            value={form.schema}
            onChange={(event) =>
              setForm({ ...form, schema: event.target.value })
            }
          />
        </Field>
        {test && (
          <Notice kind={test.ok ? "success" : "error"}>
            {test.ok
              ? "Connected to PostgreSQL " +
                (test.server_version || "") +
                ". Choose a supported schema to import."
              : "Connected, but this schema contains unsupported objects."}
          </Notice>
        )}
        {test?.unsupported?.map((entry, index) => {
          const item = entry as {
            object?: string;
            name?: string;
            reason?: string;
          };
          return (
            <Notice key={index} kind="warning">
              <strong>
                {item.name || item.object || "Unsupported object"}
              </strong>
              : {item.reason || String(entry)}
            </Notice>
          );
        })}
        {error && <Notice kind="error">{error}</Notice>}
        <div className="modal-actions">
          <button
            type="button"
            className="button-quiet"
            onClick={testConnection}
            disabled={busy}
          >
            Test access
          </button>
          <button type="submit" disabled={busy || test?.ok === false}>
            {busy ? "Starting import" : "Import baseline"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
