import { useEffect, useRef, useState } from "react";

type CodeBlockProps = {
  command: string;
  copyLabel: string;
  eyebrow?: string;
  title: string;
};

export function CodeBlock({ command, copyLabel, eyebrow, title }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => {
    if (resetTimer.current !== undefined) window.clearTimeout(resetTimer.current);
  }, []);

  const copy = async () => {
    if (!(await copyText(command))) return;
    setCopied(true);
    if (resetTimer.current !== undefined) window.clearTimeout(resetTimer.current);
    resetTimer.current = window.setTimeout(() => setCopied(false), 1_800);
  };

  return (
    <section className="site-code-card">
      <div className="site-code-card-heading">
        <div>
          {eyebrow && <p className="site-card-eyebrow">{eyebrow}</p>}
          <h3>{title}</h3>
        </div>
        <button aria-label={copyLabel} className="site-copy-button" onClick={() => void copy()} type="button">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre><code>{command}</code></pre>
    </section>
  );
}

async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // Use the narrowly scoped legacy fallback below when clipboard access is denied.
  }

  if (typeof document.execCommand !== "function") return false;
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  return copied;
}
