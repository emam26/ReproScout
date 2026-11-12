import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  ApiError,
  getAgentHandoff,
  getConfig,
  getEnvironmentEvidence,
  getEnvironmentReport,
  getEvents,
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
type Workflow = "preflight" | "audit";

export default function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [config, setConfig] = useState<ConfigStatus | null>(null);
  const [selected, setSelected] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [evidence, setEvidence] = useState<EnvironmentEvidence | null>(null);
  const [artifactLabel, setArtifactLabel] = useState("report");
  const [workflow, setWorkflow] = useState<Workflow>("preflight");
  const [form, setForm] = useState(initialForm);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshRuns = useCallback(async () => {
    try {
      setError(null);
      setRuns(await listRuns());
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshSelected = useCallback(async (runId: string) => {
    try {
      const [nextDetail, nextEvents] = await Promise.all([
        getRun(runId),
        getEvents(runId),
      ]);
      setSelected(nextDetail);
      setEvents(nextEvents);
      setEvidence(null);
      if (nextDetail.context.workflow === "environment-preflight") {
        try {
          setEvidence(await getEnvironmentEvidence(runId));
        } catch {
          // The run may be listed before its final evidence artifact is written.
        }
      }
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, []);

  const refreshConfig = useCallback(async () => {
    try {
      setConfig(await getConfig());
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, []);

  useEffect(() => {
    void refreshRuns();
    void refreshConfig();
  }, [refreshConfig, refreshRuns]);

  useEffect(() => {
    if (!selected || isTerminalStage(selected.stage)) return undefined;
    const timer = window.setInterval(() => {
      void refreshRuns();
      void refreshSelected(selected.run_id);
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [refreshRuns, refreshSelected, selected]);

  const selectRun = async (runId: string) => {
    setError(null);
    setReport(null);
    setArtifactLabel("report");
    await refreshSelected(runId);
  };

  const submitWorkflow = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!isPublicGithubUrl(form.repository_url.trim())) {
      setError("Enter a public HTTPS GitHub repository URL.");
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
      await selectRun(result.run_id);
      setForm(initialForm);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const showArtifact = async (kind: "report" | "environment" | "handoff") => {
    if (!selected) return;
    try {
      setError(null);
      const nextReport = kind === "environment"
        ? await getEnvironmentReport(selected.run_id)
        : kind === "handoff"
          ? await getAgentHandoff(selected.run_id)
          : await getReport(selected.run_id);
      setArtifactLabel(kind);
      setReport(nextReport);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const showEvidence = async () => {
    if (!selected) return;
    try {
      setError(null);
      const nextEvidence = evidence ?? (
        await getEnvironmentEvidence(selected.run_id)
      );
      setEvidence(nextEvidence);
      setArtifactLabel("environment evidence");
      setReport({
        run_id: selected.run_id,
        content: JSON.stringify(nextEvidence.evidence, null, 2),
      });
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const status = useMemo(() => {
    if (evidence?.evidence.status) return evidence.evidence.status;
    return selected?.report?.final_status;
  }, [evidence, selected]);
  const statusText = typeof status === "string" ? status : "RUNNING";

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">LOCAL CONTROL SURFACE</p>
          <h1>ReproScout</h1>
          <p className="lede">Safe environment preflight and evidence-backed reproducibility audits for open-source software.</p>
        </div>
        <div className="safety-note">
          <strong>Local only</strong>
          <span>Target code runs in Docker. Provider secrets stay server-side.</span>
        </div>
      </header>

      {error && <div className="notice danger" role="alert">{error}</div>}

      <section className="grid top-grid">
        <form className="panel audit-form" onSubmit={submitWorkflow}>
          <div className="panel-heading">
            <div>
              <p className="eyebrow">NEW WORKFLOW</p>
              <h2>Prepare a repository</h2>
            </div>
            <span className="step-chip">bounded</span>
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
              aria-label="Public GitHub URL"
              value={form.repository_url}
              onChange={(event) => setForm({ ...form, repository_url: event.target.value })}
              placeholder="https://github.com/user/repository"
              type="url"
            />
          </label>
          <div className="form-row">
            <label>
              Goal (audit only)
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
          <button className="primary" disabled={submitting} type="submit">
            {submitting ? "Running bounded checks..." : workflow === "preflight" ? "Start preflight" : "Start audit"}
          </button>
          <p className="muted">Preflight inspects requirements and verifies only permitted environment steps. It does not run experiments or training.</p>
        </form>

        <section className="panel runs-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">HISTORY</p>
              <h2>Recent runs</h2>
            </div>
            <button className="quiet-button" onClick={() => void refreshRuns()} type="button">Refresh</button>
          </div>
          {loading ? (
            <p className="empty">Loading runs...</p>
          ) : runs.length === 0 ? (
            <p className="empty">No audits yet. Start with a public repository.</p>
          ) : (
            <div className="run-list">
              {runs.map((run) => {
                const runStatus = stringValue(
                  typeof run.context.preflight_status === "string"
                    ? run.context.preflight_status
                    : run.context.final_status ?? run.outcome ?? run.stage,
                );
                const label = run.context.workflow === "environment-preflight" ? "PREFLIGHT" : "AUDIT";
                return (
                  <button
                    className={`run-row ${selected?.run_id === run.run_id ? "selected" : ""}`}
                    key={run.run_id}
                    onClick={() => void selectRun(run.run_id)}
                    type="button"
                  >
                    <span>
                      <strong>{run.run_id}</strong>
                      <small>{label} · {run.stage} · {formatDate(run.updated_at)}</small>
                    </span>
                    <span className={`status-pill ${statusTone(String(runStatus))}`}>{runStatus}</span>
                  </button>
                );
              })}
            </div>
          )}
        </section>
      </section>

      <section className="panel config-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">SETTINGS / CONFIG</p>
            <h2>Runtime status</h2>
          </div>
          <button className="quiet-button" onClick={() => void refreshConfig()} type="button">Refresh</button>
        </div>
        {!config ? (
          <p className="empty">Loading local capability status...</p>
        ) : (
          <div className="config-grid">
            <ConfigFact label="API / Python" value={`${config.python} · ${config.llm_provider}`} />
            <ConfigFact label="Docker" value={config.docker.available ? "available" : "unavailable"} ok={config.docker.available} />
            <ConfigFact label="LLM" value={config.llm_configured ? "configured" : "offline / unconfigured"} ok={config.llm_configured} />
            <ConfigFact label="Runs directory" value={config.runs_dir_writable ? "writable" : "not writable"} ok={config.runs_dir_writable} />
          </div>
        )}
      </section>

      <section className="panel detail-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">RUN DETAIL</p>
            <h2>{selected ? selected.run_id : "Select a run"}</h2>
          </div>
          {selected && <span className={`status-pill large ${statusTone(statusText)}`}>{statusText}</span>}
        </div>
        {!selected ? (
          <p className="empty">Choose a run to inspect its evidence, timeline, and artifacts.</p>
        ) : (
          <div className="detail-content">
            <div className="facts-grid">
              <Fact label="Stage" value={selected.stage} />
              <Fact label="Outcome" value={selected.outcome ?? "Active"} />
              <Fact label="Workflow" value={selected.context.workflow === "environment-preflight" ? "Environment preflight" : "Legacy audit"} />
              <Fact label="Repository" value={stringValue(selected.context.repository ?? selected.context.repository_url)} />
              <Fact label="Verification" value={stringValue(status ?? "Not available")} />
              <Fact label="Events" value={String(selected.event_count)} />
            </div>
            <div className="detail-columns">
              <section>
                <h3>Timeline</h3>
                {events.length === 0 ? <p className="muted">No events recorded.</p> : (
                  <ol className="timeline">
                    {events.map((event) => <li key={event.sequence}><span className="timeline-dot" /><div><strong>{event.event_type}</strong><small>{event.stage} · {formatDate(event.timestamp)}</small></div></li>)}
                  </ol>
                )}
              </section>
              <section>
                <h3>Evidence summary</h3>
                <EvidenceList report={selected.report} evidence={evidence?.evidence ?? null} />
              </section>
            </div>
            <div className="actions-row">
              {selected.context.workflow === "environment-preflight" ? (
                <>
                  <button className="secondary" onClick={() => void showArtifact("environment")} type="button">Environment report</button>
                  <button className="secondary" onClick={() => void showEvidence()} type="button">Evidence JSON</button>
                  <button className="secondary" onClick={() => void showArtifact("handoff")} type="button">Agent handoff</button>
                </>
              ) : (
                <button className="secondary" onClick={() => void showArtifact("report")} type="button">Load report</button>
              )}
              {report && <span className="muted">{artifactLabel} loaded ({report.content.length.toLocaleString()} characters)</span>}
            </div>
            {report && <pre className="report-preview">{report.content}</pre>}
          </div>
        )}
      </section>
    </main>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div className="fact"><span>{label}</span><strong>{value}</strong></div>;
}

function ConfigFact({ label, value, ok = true }: { label: string; value: string; ok?: boolean }) {
  return <div className="config-fact"><span>{label}</span><strong className={ok ? "is-ok" : "is-muted"}>{value}</strong></div>;
}

function EvidenceList({ report, evidence }: { report: Record<string, unknown> | null; evidence: Record<string, unknown> | null }) {
  if (evidence) {
    const assessment = isRecord(evidence.assessment) ? evidence.assessment : evidence;
    const requirements = isRecord(assessment) && Array.isArray(assessment.requirements) ? assessment.requirements.length : 0;
    const commands = isRecord(assessment) && Array.isArray(assessment.commands) ? assessment.commands.length : 0;
    const unresolved = isRecord(assessment) && Array.isArray(assessment.unresolved_problems) ? assessment.unresolved_problems.length : 0;
    const unverified = isRecord(assessment) && Array.isArray(assessment.unverified_capabilities) ? assessment.unverified_capabilities.length : 0;
    const verified = isRecord(assessment) && Array.isArray(assessment.verified_facts) ? assessment.verified_facts.length : 0;
    return <div className="evidence-list"><div><span>Requirements</span><strong>{requirements}</strong></div><div><span>Permitted commands</span><strong>{commands}</strong></div><div><span>Verified facts</span><strong>{verified}</strong></div><div><span>Unresolved problems</span><strong>{unresolved}</strong></div><div><span>Unverified capabilities</span><strong>{unverified}</strong></div><div><span>Environment</span><strong>{evidence.verified_environment === true ? "verified" : "unverified"}</strong></div><div><span>Commit</span><strong>{stringValue(evidence.commit_sha).slice(0, 12)}</strong></div></div>;
  }
  if (!report) return <p className="muted">Final report is not available yet.</p>;
  const sections = [
    ["Failures", report.failures],
    ["Diagnoses", report.diagnoses],
    ["Repairs", report.repairs],
    ["Blockers", report.blockers],
  ] as const;
  return <div className="evidence-list">{sections.map(([label, value]) => <div key={label}><span>{label}</span><strong>{Array.isArray(value) ? value.length : 0}</strong></div>)}</div>;
}

function stringValue(value: unknown): string {
  return typeof value === "string" && value.length > 0 ? value : "-";
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function errorMessage(reason: unknown): string {
  if (reason instanceof ApiError) return reason.message;
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
