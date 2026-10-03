import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  getEvents,
  getReport,
  getRun,
  isTerminalStage,
  listRuns,
  startAudit,
  statusTone,
} from "./api";
import type { EventRecord, ReportResponse, RunDetail, RunSummary } from "./types";
import "./styles.css";

const initialForm = { repository_url: "", goal: "auto", no_ai: true };

export default function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [report, setReport] = useState<ReportResponse | null>(null);
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
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, []);

  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  useEffect(() => {
    if (!selected || isTerminalStage(selected.stage)) return undefined;
    const timer = window.setInterval(() => {
      void refreshRuns();
      void refreshSelected(selected.run_id);
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [refreshRuns, refreshSelected, selected]);

  const selectRun = async (runId: string) => {
    try {
      setError(null);
      await refreshSelected(runId);
      setReport(null);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const submitAudit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.repository_url.trim()) {
      setError("Enter a public HTTPS GitHub repository URL.");
      return;
    }
    try {
      setSubmitting(true);
      setError(null);
      const result = await startAudit({ ...form, repository_url: form.repository_url.trim() });
      await refreshRuns();
      await selectRun(result.run_id);
      setForm(initialForm);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const showReport = async () => {
    if (!selected) return;
    try {
      setError(null);
      setReport(await getReport(selected.run_id));
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const status = useMemo(
    () => selected?.report?.final_status,
    [selected],
  );
  const statusText = typeof status === "string" ? status : "RUNNING";

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">LOCAL CONTROL SURFACE</p>
          <h1>ReproScout</h1>
          <p className="lede">Evidence-backed reproducibility audits for open-source software.</p>
        </div>
        <div className="safety-note">
          <strong>Local only</strong>
          <span>Target code runs in Docker. Provider secrets stay server-side.</span>
        </div>
      </header>

      {error && <div className="notice danger" role="alert">{error}</div>}

      <section className="grid top-grid">
        <form className="panel audit-form" onSubmit={submitAudit}>
          <div className="panel-heading">
            <div>
              <p className="eyebrow">NEW AUDIT</p>
              <h2>Test a repository</h2>
            </div>
            <span className="step-chip">bounded</span>
          </div>
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
              Goal
              <select
                aria-label="Goal"
                value={form.goal}
                onChange={(event) => setForm({ ...form, goal: event.target.value })}
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
            {submitting ? "Starting…" : "Start audit"}
          </button>
          <p className="muted">Audits are bounded and may take time while Docker builds the isolated workflow.</p>
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
            <p className="empty">Loading runs…</p>
          ) : runs.length === 0 ? (
            <p className="empty">No audits yet. Start with a public repository.</p>
          ) : (
            <div className="run-list">
              {runs.map((run) => {
                const runStatus = typeof run.context.final_status === "string" ? run.context.final_status : run.outcome ?? run.stage;
                return (
                  <button
                    className={`run-row ${selected?.run_id === run.run_id ? "selected" : ""}`}
                    key={run.run_id}
                    onClick={() => void selectRun(run.run_id)}
                    type="button"
                  >
                    <span>
                      <strong>{run.run_id}</strong>
                      <small>{run.stage} · {formatDate(run.updated_at)}</small>
                    </span>
                    <span className={`status-pill ${statusTone(runStatus)}`}>{runStatus}</span>
                  </button>
                );
              })}
            </div>
          )}
        </section>
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
          <p className="empty">Choose a run to inspect its evidence, timeline, and report.</p>
        ) : (
          <div className="detail-content">
            <div className="facts-grid">
              <Fact label="Stage" value={selected.stage} />
              <Fact label="Outcome" value={selected.outcome ?? "Active"} />
              <Fact label="Repository" value={stringValue(selected.context.repository)} />
              <Fact label="Verification" value={stringValue(selected.report?.final_status ?? "Not available")} />
              <Fact label="Events" value={String(selected.event_count)} />
              <Fact label="Updated" value={formatDate(selected.updated_at)} />
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
                <EvidenceList report={selected.report} />
              </section>
            </div>
            <div className="actions-row">
              <button className="secondary" onClick={() => void showReport()} type="button">Load report</button>
              {report && <span className="muted">Report loaded ({report.content.length.toLocaleString()} characters)</span>}
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

function EvidenceList({ report }: { report: Record<string, unknown> | null }) {
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
  return typeof value === "string" && value.length > 0 ? value : "—";
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
