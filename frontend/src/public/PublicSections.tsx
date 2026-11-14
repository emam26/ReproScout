import { repositoryUrl, workflow } from "./content";

export function HeroSection() {
  return (
    <section aria-labelledby="hero-title" className="site-hero" id="overview">
      <div className="site-hero-copy">
        <p className="site-kicker">Research environment preflight</p>
        <h1 id="hero-title">From research code to verified environments.</h1>
        <p className="site-lede">
          Inspect research repositories, prepare isolated environments, and generate
          evidence-backed reports before you invest in an experiment.
        </p>
        <div className="site-actions">
          <a className="site-button site-button-primary" href="#installation">
            Get started <span aria-hidden="true">{"\u2192"}</span>
          </a>
          <a className="site-button site-button-secondary" href={repositoryUrl} rel="noreferrer" target="_blank">
            View on GitHub <span aria-hidden="true">{"\u2197"}</span>
          </a>
        </div>
        <p className="site-boundary">
          Environment readiness is not scientific reproducibility. ReproScout does
          not automatically run target experiments, training, benchmarks, or tests.
        </p>
      </div>
      <div aria-label="Representative ReproScout command flow" className="site-terminal">
        <div className="site-terminal-chrome">
          <span>local terminal</span>
          <span aria-hidden="true">{"\u25cf \u25cf \u25cf"}</span>
        </div>
        <pre><code><span className="site-terminal-prompt">$</span> reproscout doctor{`\n`}<span className="site-terminal-note"># inspect local prerequisites without changing state</span>{`\n\n`}<span className="site-terminal-prompt">$</span> reproscout setup --dry-run{`\n`}<span className="site-terminal-note"># preview supported onboarding without installing anything</span>{`\n\n`}<span className="site-terminal-prompt">$</span> reproscout preflight https://github.com/OWNER/REPOSITORY</code></pre>
        <p>Representative supported commands. This static site does not run them.</p>
      </div>
    </section>
  );
}

export function WorkflowSection() {
  return (
    <section aria-labelledby="workflow-title" className="site-section" id="how-it-works">
      <SectionHeading
        eyebrow="How it works"
        id="workflow-title"
        title="A deliberate path from repository URL to evidence."
        description="Repository instructions are untrusted input. Deterministic checks and observed command outcomes determine the result."
      />
      <ol className="site-workflow">
        {workflow.map((step) => (
          <li key={step.number}>
            <span>{step.number}</span>
            <h3>{step.title}</h3>
            <p>{step.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

type SectionHeadingProps = {
  description: string;
  eyebrow: string;
  id: string;
  title: string;
};

function SectionHeading({ description, eyebrow, id, title }: SectionHeadingProps) {
  return (
    <div className="site-section-heading">
      <p className="site-kicker">{eyebrow}</p>
      <h2 id={id}>{title}</h2>
      <p>{description}</p>
    </div>
  );
}
