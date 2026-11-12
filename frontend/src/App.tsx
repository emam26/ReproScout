import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  ApiError,
  ApiUnavailableError,
  getAgentHandoff,
  getConfig,
  getEnvironmentEvidence,
  getEnvironmentReport,
  getEvents,
  getHealth,
  getReport,
  getRun,
  isTerminalStage,
  listRuns,
  startAudit,
  startPreflight,
  statusTone,
} from "./api";
import type {
  ConfigStatus,
  EnvironmentEvidence,
  EventRecord,
  ReportResponse,
  RunDetail,
  RunSummary,
} from "./types";
import "./styles.css";

const initialForm = { repository_url: "", goal: "auto", no_ai: true };
const artifactNames = {
  environment: "environment-report.md",
  evidence: "environment-evidence.json",
  handoff: "agent-handoff.md",
  report: "report.md",
} as const;

type ArtifactKind = keyof typeof artifactNames;
type ConnectionState = "checking" | "available" | "unavailable";
type Workflow = "preflight" | "audit";

type LoadedArtifact = {
  content: string;
  filename: string;
  label: string;
  mediaType: string;
};

export default function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [config, setConfig] = useState<ConfigStatus | null>(null);
  const [selected, setSelected] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [evidence, setEvidence] = useState<EnvironmentEvidence | null>(null);
  const [artifact, setArtifact] = useState<LoadedArtifact | null>(null);
  const [workflow, setWorkflow] = useState<Workflow>("preflight");
  const [form, setForm] = useState(initialForm);
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [apiVersion, setApiVersion] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshRuns = useCallback(async () => {
    setRuns(await listRuns());
  }, []);

  const refreshConfig = useCallback(async () => {
    setConfig(await getConfig());
  }, []);

  const refreshDashboard = useCallback(async () => {
    setConnection("checking");
    setLoading(true);
    setError(null);
    try {
      const health = await getHealth();
      if (health.status !== "ok") {
        throw new ApiError("The local API returned an unhealthy status.", 503);
      }
      setApiVersion(health.version);
      setConnection("available");
    } catch (reason) {
      setApiVersion(null);
      setConfig(null);
      setRuns([]);
      setConnection("unavailable");
      setError(errorMessage(reason));
      setLoading(false);
      return;
    }
    try {
      await Promise.all([refreshConfig(), refreshRuns()]);
    } catch (reason) {
      if (isConnectionFailure(reason)) {
        setConnection("unavailable");
        setConfig(null);
        setRuns([]);
      }
      setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, [refreshConfig, refreshRuns]);

  const refreshSelected = useCallback(async (runId: string) => {
    const [nextDetail, nextEvents] = await Promise.all([
      getRun(runId),
      getEvents(runId),
    ]);
    setSelected(nextDetail);
    setEvents(nextEvents);
    setArtifact(null);
    setEvidence(null);
    if (isPreflight(nextDetail)) {
      try {
        setEvidence(await getEnvironmentEvidence(runId));
      } catch (reason) {
        if (isConnectionFailure(reason)) throw reason;
        // A persisted run may legitimately precede its final evidence artifact.
      }
    }
  }, []);

  useEffect(() => {
    void refreshDashboard();
  }, [refreshDashboard]);

  useEffect(() => {
    if (!selected || isTerminalStage(selected.stage)) return undefined;
    const timer = window.setInterval(() => {
      void refreshRuns().catch(handlePollingFailure);
      void refreshSelected(selected.run_id).catch(handlePollingFailure);
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [refreshRuns, refreshSelected, selected]);

  const handleFailure = (reason: unknown) => {
    if (isConnectionFailure(reason)) {
      setConnection("unavailable");
    }
    setError(errorMessage(reason));
  };

  const handlePollingFailure = (reason: unknown) => {
    if (isConnectionFailure(reason)) {
      setConnection("unavailable");
      setError(errorMessage(reason));
    }
  };

  const selectRun = async (runId: string) => {
    setError(null);
    try {
      await refreshSelected(runId);
    } catch (reason) {
      handleFailure(reason);
    }
  };

  const submitWorkflow = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!isPublicGithubUrl(form.repository_url.trim())) {
      setError("Enter a public HTTPS GitHub repository URL.");
      return;
    }
    if (connection !== "available") {
      setError("Connect to the local API before starting a preflight.");
      return;
    }
    try {
      setSubmitting(true);
      setError(null);
      const result = workflow === "preflight"
        ? await startPreflight({
            no_ai: form.no_ai,
            repository_url: form.repository_url.trim(),
          })
        : await startAudit({
            ...form,
            repository_url: form.repository_url.trim(),
          });
      await refreshRuns();
      await refreshSelected(result.run_id);
      setForm(initialForm);
    } catch (reason) {
      handleFailure(reason);
    } finally {
      setSubmitting(false);
    }
  };

  const loadArtifact = async (kind: ArtifactKind): Promise<LoadedArtifact> => {
    if (!selected) throw new Error("Select a run before loading its reports.");
    if (kind === "evidence") {
      const nextEvidence = evidence ?? await getEnvironmentEvidence(selected.run_id);
      setEvidence(nextEvidence);
      return {
        content: JSON.stringify(nextEvidence.evidence, null, 2),
        filename: artifactNames.evidence,
        label: "Environment evidence",
        mediaType: "application/json",
      };
    }
    const response = kind === "environment"
      ? await getEnvironmentReport(selected.run_id)
      : kind === "handoff"
        ? await getAgentHandoff(selected.run_id)
        : await getReport(selected.run_id);
    return artifactFromResponse(kind, response);
  };

  const viewArtifact = async (kind: ArtifactKind) => {
    try {
      setError(null);
      setArtifact(await loadArtifact(kind));
    } catch (reason) {
      handleFailure(reason);
    }
  };

  const downloadArtifact = async (kind: ArtifactKind) => {
    try {
      setError(null);
      const nextArtifact = await loadArtifact(kind);
      setArtifact(nextArtifact);
      downloadText(nextArtifact);
    } catch (reason) {
      handleFailure(reason);
    }
  };

  const readiness = useMemo(
    () => preflightAssessment(evidence?.evidence ?? null),
    [evidence],
  );
  const status = readiness?.status ?? selected?.report?.final_status;
  const statusText = stringValue(status ?? "RUNNING");

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">LOCAL RESEARCH ENVIRONMENT PREFLIGHT</p>
          <h1>ReproScout</h1>
          <p className="lede">
            Submit a public repository, verify permitted setup in Docker, and
            export evidence for the next engineering decision.
          </p>
        </div>
        <section className={`connection-card ${connection}`} aria-live="polite">
          <span className="connection-label">Local API</span>
          <strong>{connectionLabel(connection, apiVersion)}</strong>
          <span>
            {connection === "available"
              ? "Docker status is reported separately below."
              : "The dashboard never falls back to a mock backend."}
          </span>
          <button
            className="quiet-button"
            onClick={() => void refreshDashboard()}
            type="button"
          >
            Retry connection
          </button>
        </section>
      </header>

      {error && <div className="notice danger" role="alert">{error}</div>}

      <section className="workflow-guide" aria-label="Preflight workflow">
        <span>1. Submit repository</span>
        <span>2. Run bounded preflight</span>
        <span>3. Inspect readiness</span>
        <span>4. Export evidence</span>
      </section>

      <section className="grid top-grid">
        <form className="panel audit-form" onSubmit={submitWorkflow}>
          <div className="panel-heading">
            <div>
              <p className="eyebrow">NEW PREFLIGHT</p>
              <h2>Prepare a repository</h2>
            </div>
            <span className="step-chip">safe by default</span>
          </div>
          <label>
            Workflow
            <select
              aria-label="Workflow"
              value={workflow}
              onChange={(event) => setWorkflow(event.target.value as Workflow)}
            >
              <option value="preflight">Environment preflight</option>
              <option value="audit">Legacy reproduction audit</option>
            </select>
          </label>
          <label>
            Public GitHub URL
            <input
              aria-describedby="repository-url-help"
              aria-label="Public GitHub URL"
              value={form.repository_url}
              onChange={(event) => setForm({
                ...form,
                repository_url: event.target.value,
              })}
              placeholder="https://github.com/user/repository"
              type="url"
            />
          </label>
          <p className="field-help" id="repository-url-help">
            Preflight is the default. It never starts target tests, demos,
            training, evaluations, or experiments.
          </p>
          <div className="form-row">
            <label>
              Goal (legacy audit only)
              <select
                aria-label="Goal"
                value={form.goal}
                onChange={(event) => setForm({ ...form, goal: event.target.value })}
                disabled={workflow === "preflight"}
              >
                <option value="auto">Auto</option>
                <option value="install">Install</option>
                <option value="tests">Tests</option>
                <option value="demo">Demo</option>
              </select>
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={form.no_ai}
                onChange={(event) => setForm({ ...form, no_ai: event.target.checked })}
              />
              No AI interpretation
            </label>
          </div>
          <button
            className="primary"
            disabled={submitting || connection !== "available"}
            type="submit"
          >
            {submitting
              ? "Running bounded checks..."
              : workflow === "preflight"
                ? "Start preflight"
                : "Start legacy audit"}
          </button>
        </form>

        <section className="panel runs-panel" aria-labelledby="history-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">HISTORY</p>
              <h2 id="history-title">Recent runs</h2>
            </div>
            <button
              className="quiet-button"
              onClick={() => void refreshDashboard()}
              type="button"
            >
              Refresh
            </button>
          </div>
          {loading ? (
            <p className="empty">Checking the local API and loading persisted runs...</p>
          ) : connection !== "available" ? (
            <p className="empty">History is unavailable until the local API is connected.</p>
          ) : runs.length === 0 ? (
            <p className="empty">No runs yet. Start with an environment preflight.</p>
          ) : (
            <div className="run-list">
              {runs.map((run) => {
                const runStatus = stringValue(
                  typeof run.context.preflight_status === "string"
                    ? run.context.preflight_status
                    : run.context.final_status ?? run.outcome ?? run.stage,
                );
                const label = isPreflight(run) ? "PREFLIGHT" : "LEGACY AUDIT";
                return (
                  <button
                    aria-pressed={selected?.run_id === run.run_id}
                    className={`run-row ${selected?.run_id === run.run_id ? "selected" : ""}`}
                    key={run.run_id}
                    onClick={() => void selectRun(run.run_id)}
                    type="button"
                  >
                    <span>
                      <strong>{run.run_id}</strong>
                      <small>{label} / {run.stage} / {formatDate(run.updated_at)}</small>
                    </span>
                    <span className={`status-pill ${statusTone(runStatus)}`}>{runStatus}</span>
                  </button>
                );
              })}
            </div>
          )}
        </section>
      </section>

      <section className="panel config-panel" aria-labelledby="runtime-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">LOCAL CAPABILITIES</p>
            <h2 id="runtime-title">API and Docker status</h2>
          </div>
          <button
            className="quiet-button"
            onClick={() => void refreshDashboard()}
            type="button"
          >
            Refresh status
          </button>
        </div>
        {!config ? (
          <p className="empty">Capability status is unavailable while the API is offline.</p>
        ) : (
          <>
            <div className="config-grid">
              <ConfigFact label="API / Python" value={`${apiVersion ?? "unknown"} / ${config.python}`} />
              <ConfigFact
                label="Docker capability checks"
                value={config.docker.available ? "ready" : "unverified"}
                ok={config.docker.available}
              />
              <ConfigFact
                label="Docker daemon"
                value={config.docker.daemon_running ? "reachable" : "unavailable"}
                ok={config.docker.daemon_running === true}
              />
              <ConfigFact
                label="Sandbox seccomp"
                value={config.docker.seccomp ?? "unverified"}
                ok={config.docker.seccomp === "verified"}
              />
              <ConfigFact label="Runs directory" value={config.runs_dir_writable ? "writable" : "not writable"} ok={config.runs_dir_writable} />
              <ConfigFact label="LLM mode" value={config.llm_configured ? "configured" : "offline / unconfigured"} ok={config.llm_configured} />
            </div>
            <p className="muted">
              These are capability checks from the local API, not proof that a
              disposable sandbox completed a readiness probe. A preflight records
              its own Docker evidence.
            </p>
          </>
        )}
      </section>

      <section className="panel detail-panel" aria-labelledby="run-detail-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">RUN DETAIL</p>
            <h2 id="run-detail-title">{selected ? selected.run_id : "Select a run"}</h2>
          </div>
          {selected && <span className={`status-pill large ${statusTone(statusText)}`}>{statusText}</span>}
        </div>
        {!selected ? (
          <p className="empty">Choose a run to inspect persisted stages, evidence, and reports.</p>
        ) : (
          <div className="detail-content">
            <div className="facts-grid">
              <Fact label="Stage" value={selected.stage} />
              <Fact label="Outcome" value={selected.outcome ?? "Active"} />
              <Fact label="Workflow" value={isPreflight(selected) ? "Environment preflight" : "Legacy audit"} />
              <Fact label="Repository" value={stringValue(selected.context.repository ?? selected.context.repository_url)} />
              <Fact label="Readiness" value={statusText} />
              <Fact label="Events" value={String(selected.event_count)} />
            </div>
            {isPreflight(selected) ? (
              <PreflightReadiness evidence={evidence?.evidence ?? null} />
            ) : (
              <LegacySummary report={selected.report} />
            )}
            <div className="detail-columns">
              <section>
                <h3>Persisted stages</h3>
                {events.length === 0 ? <p className="muted">No events recorded yet.</p> : (
                  <ol className="timeline">
                    {events.map((event) => (
                      <li key={event.sequence}>
                        <span className="timeline-dot" />
                        <div>
                          <strong>{event.event_type}</strong>
                          <small>{event.stage} / {formatDate(event.timestamp)}</small>
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </section>
              <ReportActions
                isPreflight={isPreflight(selected)}
                onDownload={downloadArtifact}
                onView={viewArtifact}
              />
            </div>
            {artifact && (
              <section aria-label={`${artifact.label} preview`}>
                <div className="artifact-heading">
                  <h3>{artifact.label}</h3>
                  <span className="muted">{artifact.content.length.toLocaleString()} characters</span>
                </div>
                <pre className="report-preview">{artifact.content}</pre>
              </section>
            )}
          </div>
        )}
      </section>
    </main>
  );
}

function ReportActions({
  isPreflight,
  onDownload,
  onView,
}: {
  isPreflight: boolean;
  onDownload: (kind: ArtifactKind) => void;
  onView: (kind: ArtifactKind) => void;
}) {
  const artifacts: Array<{ kind: ArtifactKind; label: string }> = isPreflight
    ? [
        { kind: "environment", label: "Environment report" },
        { kind: "evidence", label: "Evidence JSON" },
        { kind: "handoff", label: "Agent handoff" },
      ]
    : [{ kind: "report", label: "Audit report" }];

  return (
    <section aria-labelledby="reports-title">
      <h3 id="reports-title">Reports</h3>
      <p className="muted">Artifacts are bounded and redacted by the local API.</p>
      <div className="report-actions">
        {artifacts.map((item) => (
          <div className="report-action" key={item.kind}>
            <strong>{item.label}</strong>
            <div>
              <button className="secondary" onClick={() => onView(item.kind)} type="button">View</button>
              <button className="quiet-button" onClick={() => onDownload(item.kind)} type="button">Download</button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function PreflightReadiness({ evidence }: { evidence: Record<string, unknown> | null }) {
  const assessment = preflightAssessment(evidence);
  const inspection = isRecord(evidence?.inspection) ? evidence.inspection : null;
  if (!assessment) {
    return <p className="empty">Preflight evidence is not available yet. The run may still be incomplete.</p>;
  }
  const requirements = recordList(assessment.requirements);
  const blockers = stringList(assessment.unresolved_problems);
  const warnings = stringList(assessment.unverified_capabilities);
  const nextSteps = stringList(assessment.safe_next_steps);
  const pythonConstraints = stringList(inspection?.python_constraints);
  const dependencyCount = Array.isArray(inspection?.package_requirements)
    ? inspection.package_requirements.length
    : requirements.filter((item) => item.kind === "PACKAGE").length;

  return (
    <section className="readiness-panel" aria-labelledby="readiness-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">ENVIRONMENT READINESS</p>
          <h3 id="readiness-title">{stringValue(assessment.summary)}</h3>
        </div>
        <span className={`status-pill ${statusTone(stringValue(assessment.status))}`}>
          {assessment.verified_environment === true ? "ENVIRONMENT VERIFIED" : "NOT VERIFIED"}
        </span>
      </div>
      <div className="readiness-grid">
        <Fact label="Pinned commit" value={stringValue(evidence?.commit_sha).slice(0, 12)} />
        <Fact label="Python compatibility" value={pythonConstraints.join(", ") || "not declared"} />
        <Fact label="Dependencies detected" value={String(dependencyCount)} />
        <Fact label="Prerequisites" value={String(requirements.length)} />
      </div>
      <div className="finding-grid">
        <FindingList title="Blockers" values={blockers} empty="No unresolved problem was recorded." tone="danger" />
        <FindingList title="Warnings" values={warnings} empty="No unverified capability was recorded." tone="warning" />
        <FindingList title="Safe next steps" values={nextSteps} empty="No next step was recorded." tone="neutral" />
      </div>
      <details className="requirements-details">
        <summary>Detected dependencies and prerequisites ({requirements.length})</summary>
        {requirements.length === 0 ? <p className="muted">No prerequisite records are available.</p> : (
          <ul className="finding-list neutral">
            {requirements.map((item, index) => <li key={`${item.name}-${index}`}>{requirementText(item)}</li>)}
          </ul>
        )}
      </details>
    </section>
  );
}

function LegacySummary({ report }: { report: Record<string, unknown> | null }) {
  if (!report) return <p className="empty">A legacy audit report is not available yet.</p>;
  return (
    <section className="readiness-panel" aria-labelledby="legacy-summary-title">
      <p className="eyebrow">LEGACY AUDIT</p>
      <h3 id="legacy-summary-title">Audit evidence summary</h3>
      <div className="readiness-grid">
        <Fact label="Failures" value={String(arrayLength(report.failures))} />
        <Fact label="Diagnoses" value={String(arrayLength(report.diagnoses))} />
        <Fact label="Repairs" value={String(arrayLength(report.repairs))} />
        <Fact label="Blockers" value={String(arrayLength(report.blockers))} />
      </div>
    </section>
  );
}

function FindingList({
  empty,
  title,
  tone,
  values,
}: {
  empty: string;
  title: string;
  tone: "danger" | "warning" | "neutral";
  values: string[];
}) {
  return (
    <section className="finding-card">
      <h4>{title}</h4>
      {values.length === 0 ? <p className="muted">{empty}</p> : (
        <ul className={`finding-list ${tone}`}>
          {values.map((value, index) => <li key={`${value}-${index}`}>{value}</li>)}
        </ul>
      )}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div className="fact"><span>{label}</span><strong title={value}>{value}</strong></div>;
}

function ConfigFact({ label, value, ok = true }: { label: string; value: string; ok?: boolean }) {
  return <div className="config-fact"><span>{label}</span><strong className={ok ? "is-ok" : "is-muted"} title={value}>{value}</strong></div>;
}

function artifactFromResponse(kind: Exclude<ArtifactKind, "evidence">, response: ReportResponse): LoadedArtifact {
  return {
    content: response.content,
    filename: artifactNames[kind],
    label: kind === "environment" ? "Environment report" : kind === "handoff" ? "Agent handoff" : "Audit report",
    mediaType: "text/plain;charset=utf-8",
  };
}

function downloadText(artifact: LoadedArtifact) {
  if (typeof URL.createObjectURL !== "function") {
    throw new Error("Downloads are not supported by this browser.");
  }
  const url = URL.createObjectURL(new Blob([artifact.content], { type: artifact.mediaType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = artifact.filename;
  link.style.display = "none";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function connectionLabel(connection: ConnectionState, version: string | null): string {
  if (connection === "checking") return "Checking connection";
  if (connection === "unavailable") return "API unavailable";
  return version ? `Connected (v${version})` : "Connected";
}

function preflightAssessment(value: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!value) return null;
  return isRecord(value.assessment) ? value.assessment : value;
}

function isPreflight(run: Pick<RunSummary, "context">): boolean {
  return run.context.workflow === "environment-preflight";
}

function isConnectionFailure(reason: unknown): boolean {
  return reason instanceof ApiUnavailableError;
}

function requirementText(value: Record<string, unknown>): string {
  const name = stringValue(value.name);
  const expected = stringValue(value.expected);
  const state = stringValue(value.state);
  return [name, expected !== "-" ? `expected ${expected}` : "", state].filter(Boolean).join(" / ");
}

function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function arrayLength(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function stringValue(value: unknown): string {
  return typeof value === "string" && value.length > 0 ? value : "-";
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function errorMessage(reason: unknown): string {
  if (reason instanceof Error) return reason.message;
  return "The local API could not be reached.";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPublicGithubUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    const parts = parsed.pathname.split("/").filter(Boolean);
    return parsed.protocol === "https:"
      && parsed.hostname.toLowerCase() === "github.com"
      && parts.length === 2
      && !parsed.username
      && !parsed.password
      && !parsed.search
      && !parsed.hash;
  } catch {
    return false;
  }
}
