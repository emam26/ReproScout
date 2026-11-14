export const repositoryUrl = "https://github.com/emam26/ReproScout";
export const navigation = [
  { id: "how", label: "How it works" },
  { id: "install", label: "Install" },
  { id: "outputs", label: "Reports" },
  { id: "scope", label: "Scope" },
  { id: "docs", label: "Docs" },
] as const;

export const cloneCommands = [
  "git clone https://github.com/emam26/ReproScout.git",
  "cd ReproScout",
].join("\n");

export const installCommands = {
  uv: "uv tool install --editable .",
  pip: [
    "python -m venv .venv",
    "# Activate .venv for your shell, then:",
    "python -m pip install -e .",
  ].join("\n"),
} as const;

export const prerequisiteCommands = [
  "reproscout doctor",
  "reproscout setup --dry-run",
  "reproscout setup",
].join("\n");

export const preflightCommands = "reproscout preflight https://github.com/OWNER/REPOSITORY";

export const workflow = [
  {
    id: "inspect",
    number: "1",
    title: "Inspect",
    lead: "Read the repository to learn what it needs.",
    items: [
      "Finds Python, package, environment, and system prerequisites in metadata and setup material.",
      "Notes declared dependencies and setup instructions.",
      "Treats the repository and its instructions as untrusted input.",
    ],
  },
  {
    id: "prepare",
    number: "2",
    title: "Prepare",
    lead: "Build a bounded plan, then run only what it permits.",
    items: [
      "Uses the Docker sandbox for permitted setup and lightweight verification commands.",
      "Designed to avoid running target code directly on your machine.",
      "Optional LLM reasoning can help interpret instructions and errors.",
    ],
  },
  {
    id: "verify",
    number: "3",
    title: "Verify",
    lead: "Judge outcomes by evidence, not by assertion.",
    items: [
      "Evaluates command outcomes, files, and environment checks.",
      "Reports verified facts separately from incomplete, blocked, unsafe, or unverified conditions.",
      "Writes a report, structured evidence, and an agent handoff.",
    ],
  },
] as const;

export const reportTabs = [
  {
    id: "report",
    name: "environment-report.md",
    audience: "For people",
    whatItIs: "A concise, human-readable readiness report you can share with a researcher.",
    whatItSeparates: "Verified readiness, partial setup, failed installation, missing prerequisites, unsafe conditions, and unverified capabilities.",
  },
  {
    id: "evidence",
    name: "environment-evidence.json",
    audience: "For programs",
    whatItIs: "Structured evidence you can inspect programmatically.",
    whatItSeparates: "Commands, outcomes, checks, and unresolved findings.",
  },
  {
    id: "handoff",
    name: "agent-handoff.md",
    audience: "For coding agents",
    whatItIs: "A focused brief for a researcher or coding agent.",
    whatItSeparates: "The pinned commit, verified environment facts, executed commands, outcomes, and safe next steps.",
  },
] as const;

export const documentationLinks = [
  { href: `${repositoryUrl}/blob/main/docs/CLI.md`, label: "CLI reference", detail: "Command contracts" },
  { href: `${repositoryUrl}/blob/main/docs/ARCHITECTURE.md`, label: "Architecture", detail: "How it fits together" },
  { href: `${repositoryUrl}/blob/main/docs/DOCKER_SANDBOX.md`, label: "Docker sandbox", detail: "Read before sensitive use" },
  { href: `${repositoryUrl}/blob/main/docs/PROJECT_SPEC.md`, label: "Project specification", detail: "Scope" },
  { href: `${repositoryUrl}/blob/main/docs/DIAGNOSTICS.md`, label: "Diagnostics", detail: "Failure handling" },
  { href: `${repositoryUrl}/blob/main/docs/ROADMAP.md`, label: "Roadmap", detail: "Planned work" },
  { href: `${repositoryUrl}/blob/main/CONTRIBUTING.md`, label: "Contributing guide", detail: "Start here" },
] as const;
