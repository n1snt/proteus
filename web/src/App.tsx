import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { Mark } from "./components/ui";
import { message } from "./lib/format";
import { JobPage } from "./pages/JobPage";
import { Landing } from "./pages/Landing";
import { Workspace } from "./pages/Workspace";
import type { Session } from "./types";

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .session()
      .then(setSession)
      .catch((reason) => setError(message(reason)));
  }, []);

  if (error)
    return (
      <main className="center-state">
        <Mark />
        <h1>Session unavailable</h1>
        <p>{error}</p>
        <button onClick={() => location.reload()}>Try again</button>
      </main>
    );
  if (!session)
    return (
      <main className="center-state">
        <LoaderCircle className="spin" />
        <p>Opening workspace</p>
      </main>
    );
  return (
    <Routes>
      <Route path="/" element={<Landing session={session} />} />
      <Route
        path="/projects/:projectId/branches/:branchId"
        element={<Workspace session={session} />}
      />
      <Route path="/jobs/:jobId" element={<JobPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
