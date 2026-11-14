import { useEffect, useRef, useState } from "react";
type CodeBlockProps = {
  ariaLabelledBy?: string;
  command: string;
  copyLabel?: string;
  hidden?: boolean;
  id?: string;
  role?: "tabpanel";
};

export function CodeBlock({ ariaLabelledBy, command, copyLabel = "Copy", hidden, id, role }: CodeBlockProps) {
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
    <div aria-labelledby={ariaLabelledBy} className="code" hidden={hidden} id={id} role={role}>
      <div className="code-head">
        <span>shell</span>
        <button aria-label={copyLabel} className="copy" onClick={() => void copy()} type="button">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre><code>{command}</code></pre>
    </div>
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
  textarea.style.cssText = "position:fixed;opacity:0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  return copied;
}
