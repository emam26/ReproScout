import { useRef, useState, type KeyboardEvent } from "react";
import { CodeBlock } from "./CodeBlock";
import {
  cloneCommands,
  documentationLinks,
  installCommands,
  preflightCommands,
  prerequisiteCommands,
  reportTabs,
  repositoryUrl,
  workflow,
} from "./content";

const ledgerRows = [
  { status: "\u2713 Verified", tone: "ok", text: "Python 3.11 requirement detected and satisfied in sandbox" },
  { status: "\u2713 Verified", tone: "ok", text: "Declared dependencies installed without error" },
  { status: "\u2715 Missing", tone: "bad", text: "System tool required by setup docs is not present" },
  { status: "\u25D0 Partial", tone: "warn", text: "Lightweight import check passed for 2 of 3 modules" },
  { status: "? Unverified", tone: "unk", text: "GPU-dependent steps were not checked" },
] as const;

export function HeroSection() {
  return (
    <div className="hero" id="top">
      <div className="wrap hero-grid">
        <div>
          <h1>Check the environment before you chase the result.</h1>
          <p className="lede">ReproScout inspects a research repository, prepares its setup in a bounded Docker sandbox, and writes a report of what worked, what failed, and what is still unverified.</p>
          <div className="cta">
            <a className="btn btn-primary" href="#install">Install from source</a>
            <a className="btn btn-quiet" href={repositoryUrl} rel="noreferrer" target="_blank">View on GitHub</a>
          </div>
        </div>
        <figure aria-label="Illustrative evidence ledger" className="ledger" style={{ margin: 0 }}>
          <div className="ledger-bar"><b>environment-report.md</b><span>OWNER/REPOSITORY @ pinned revision</span></div>
          <ol>
            {ledgerRows.map((row) => (
              <li key={row.text}>
                <span className={`st ${row.tone}`}>{row.status}</span>
                <span className="t">{row.text}</span>
              </li>
            ))}
          </ol>
          <div className="verdict">Assessment: <b>PARTIAL</b>. One prerequisite blocks full readiness.</div>
          <span className="tag">Schematic example for illustration. Not output from a real run.</span>
        </figure>
      </div>
    </div>
  );
}

export function WorkflowSection() {
  const [selected, setSelected] = useState(0);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const active = workflow[selected];

  const selectStage = (index: number, focus = false) => {
    setSelected(index);
    if (focus) tabRefs.current[index]?.focus();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    let nextIndex: number | undefined;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (selected + 1) % workflow.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (selected - 1 + workflow.length) % workflow.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = workflow.length - 1;
    if (nextIndex === undefined) return;
    event.preventDefault();
    selectStage(nextIndex, true);
  };

  return (
    <section aria-labelledby="how-h" className="s" id="how">
      <div className="wrap">
        <div className="head">
          <h2 id="how-h">Three stages, one evidence trail.</h2>
          <p className="sec-intro">Every assessment starts by recording the repository and the exact revision it used.</p>
        </div>
        <div aria-label="Workflow stages" className="track" role="tablist">
          {workflow.map((stage, index) => (
            <button
              aria-controls={`p-${stage.id}`}
              aria-selected={selected === index}
              className="node"
              id={`t-${stage.id}`}
              key={stage.id}
              onClick={() => selectStage(index)}
              onKeyDown={handleKeyDown}
              ref={(element) => { tabRefs.current[index] = element; }}
              role="tab"
              tabIndex={selected === index ? 0 : -1}
              type="button"
            >
              <span className="dot">{stage.number}</span>
              <span className="nm">{stage.title}</span>
            </button>
          ))}
        </div>
        {workflow.map((stage, index) => (
          <div aria-labelledby={`t-${stage.id}`} className="panel" hidden={selected !== index} id={`p-${stage.id}`} key={stage.id} role="tabpanel">
            <p className="lead">{stage.lead}</p>
            <ul>{stage.items.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        ))}
        <p className="note">The control flow and verifier decide status. LLM output never establishes success.</p>
      </div>
    </section>
  );
}

export function InstallationSection() {
  const [method, setMethod] = useState<"uv" | "pip">("uv");
  const methodTabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const methodTabs = [
    { id: "uv", label: "uv", command: installCommands.uv },
    { id: "venv", label: "venv + pip", command: installCommands.pip },
  ] as const;

  const selectMethod = (nextMethod: "uv" | "pip", focus = false) => {
    setMethod(nextMethod);
    if (focus) methodTabRefs.current[nextMethod === "uv" ? 0 : 1]?.focus();
  };

  const handleMethodKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const currentIndex = method === "uv" ? 0 : 1;
    let nextIndex: number | undefined;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (currentIndex + 1) % methodTabs.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (currentIndex - 1 + methodTabs.length) % methodTabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = methodTabs.length - 1;
    if (nextIndex === undefined) return;
    event.preventDefault();
    selectMethod(nextIndex === 0 ? "uv" : "pip", true);
  };

  return (
    <section aria-labelledby="in-h" className="s" id="install">
      <div className="wrap">
        <div className="head">
          <h2 id="in-h">Install from a source checkout.</h2>
          <p className="sec-intro">ReproScout is not presented as a PyPI package. Python 3.11 or later is required.</p>
        </div>
        <div className="install-grid">
          <ol className="steps">
            <li>
              <div>
                <h3>Clone the repository</h3>
                <CodeBlock command={cloneCommands} copyLabel="Copy clone commands" />
              </div>
            </li>
            <li>
              <div>
                <h3>Install the CLI</h3>
                <p>Choose uv or a standard virtual environment.</p>
                <div aria-label="Install method" className="tabs-mini" role="tablist">
                  {methodTabs.map((tab, index) => (
                    <button
                      aria-controls={`p-${tab.id}`}
                      aria-selected={method === (tab.id === "uv" ? "uv" : "pip")}
                      id={`t-${tab.id}`}
                      key={tab.id}
                      onClick={() => selectMethod(tab.id === "uv" ? "uv" : "pip")}
                      onKeyDown={handleMethodKeyDown}
                      ref={(element) => { methodTabRefs.current[index] = element; }}
                      role="tab"
                      tabIndex={method === (tab.id === "uv" ? "uv" : "pip") ? 0 : -1}
                      type="button"
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
                {methodTabs.map((tab) => (
                  <CodeBlock
                    ariaLabelledBy={`t-${tab.id}`}
                    command={tab.command}
                    copyLabel={`Copy ${tab.label} installation commands`}
                    hidden={method !== (tab.id === "uv" ? "uv" : "pip")}
                    id={`p-${tab.id}`}
                    key={tab.id}
                    role="tabpanel"
                  />
                ))}
              </div>
            </li>
            <li>
              <div>
                <h3>Check your prerequisites</h3>
                <p><code>doctor</code> is read-only and <code>--dry-run</code> changes nothing. <code>setup</code> asks for consent before any install action.</p>
                <CodeBlock command={prerequisiteCommands} copyLabel="Copy prerequisite commands" />
              </div>
            </li>
            <li>
              <div>
                <h3>Run a preflight</h3>
                <p>Replace the placeholder with a public repository.</p>
                <CodeBlock command={preflightCommands} copyLabel="Copy preflight command" />
              </div>
            </li>
          </ol>
          <aside aria-label="Before you install" className="aside">
            <h3>Good to know</h3>
            <ul>
              <li>Docker is a separate prerequisite for sandboxed work. Installing the CLI does not install or validate it.</li>
              <li>A running, healthy Docker runtime is reused, not reinstalled.</li>
              <li>Native Windows and macOS install paths still need direct platform verification.</li>
            </ul>
          </aside>
        </div>
      </div>
    </section>
  );
}

export function ReportsSection() {
  const [selected, setSelected] = useState(0);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const selectReport = (index: number, focus = false) => {
    setSelected(index);
    if (focus) tabRefs.current[index]?.focus();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    let nextIndex: number | undefined;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (selected + 1) % reportTabs.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (selected - 1 + reportTabs.length) % reportTabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = reportTabs.length - 1;
    if (nextIndex === undefined) return;
    event.preventDefault();
    selectReport(nextIndex, true);
  };

  return (
    <section aria-labelledby="out-h" className="s" id="outputs">
      <div className="wrap">
        <div className="head">
          <h2 id="out-h">Three files you can hand to a person or an agent.</h2>
          <p className="sec-intro">A completed preflight writes these to its run output. The directory is configurable.</p>
        </div>
        <div className="out-grid">
          <div aria-label="Report files" aria-orientation="vertical" className="filelist" role="tablist">
            {reportTabs.map((report, index) => (
              <button
                aria-controls={`p-${report.id}`}
                aria-selected={selected === index}
                className="file"
                id={`t-${report.id}`}
                key={report.id}
                onClick={() => selectReport(index)}
                onKeyDown={handleKeyDown}
                ref={(element) => { tabRefs.current[index] = element; }}
                role="tab"
                tabIndex={selected === index ? 0 : -1}
                type="button"
              >
                <code>{report.name}</code><span>{report.audience}</span>
              </button>
            ))}
          </div>
          <div className="preview">
            {reportTabs.map((report, index) => (
              <div aria-labelledby={`t-${report.id}`} className="panel" hidden={selected !== index} id={`p-${report.id}`} key={report.id} role="tabpanel">
                <div className="body">
                  <dl>
                    <dt>What it is</dt><dd>{report.whatItIs}</dd>
                    <dt>{index === 0 ? "What it separates" : "What it holds"}</dt><dd>{report.whatItSeparates}</dd>
                  </dl>
                </div>
              </div>
            ))}
            <span className="tag" style={{ borderTop: "1px solid var(--rule)" }}>Descriptions follow the project README. Consult a real run for exact contents.</span>
          </div>
        </div>
      </div>
    </section>
  );
}

export function ScopeSection() {
  return (
    <section aria-labelledby="sc-h" className="s" id="scope">
      <div className="wrap">
        <div className="head">
          <h2 id="sc-h">What it does, and what it does not claim.</h2>
        </div>
        <div className="scope">
          <div className="yes">
            <h3>Available in the repository</h3>
            <dl>
              <div className="row"><dt>Environment preflight</dt><dd>Detects requirements, dependencies, system tools, and missing prerequisites.</dd></div>
              <div className="row"><dt>Isolated execution</dt><dd>Controlled Docker runs for permitted setup and lightweight checks.</dd></div>
              <div className="row"><dt>Optional reasoning</dt><dd>Configured LLM providers can interpret instructions and errors.</dd></div>
              <div className="row"><dt>Interfaces</dt><dd>Python CLI and documented control surfaces.</dd></div>
            </dl>
          </div>
          <div className="no">
            <h3>Not claimed</h3>
            <dl>
              <div className="row"><dt>Reproduced research results</dt><dd>Preflight checks environment readiness, not experiments or paper claims.</dd></div>
              <div className="row"><dt>A PyPI release</dt><dd>Install from source for now.</dd></div>
              <div className="row"><dt>Perfect isolation</dt><dd>The sandbox is defense in depth, and configuration alone is not proof it works.</dd></div>
            </dl>
          </div>
        </div>
      </div>
    </section>
  );
}

export function DocumentationSection() {
  return (
    <section aria-labelledby="dc-h" className="s" id="docs">
      <div className="wrap">
        <div className="head">
          <h2 id="dc-h">Read the docs, then read the source.</h2>
          <p className="sec-intro">Contributions are welcome within the documented scope and safety model.</p>
        </div>
        <ul className="docs">
          {documentationLinks.map((link) => (
            <li key={link.label}>
              <a href={link.href} rel="noreferrer" target="_blank">{link.label}<span>{link.detail}</span></a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
