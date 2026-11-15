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
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const resetTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => {
    if (resetTimer.current !== undefined) window.clearTimeout(resetTimer.current);
  }, []);

  const copy = async () => {
    const copied = await copyText(command);
    if (resetTimer.current !== undefined) window.clearTimeout(resetTimer.current);
    setCopyState(copied ? "copied" : "failed");
    resetTimer.current = window.setTimeout(() => setCopyState("idle"), 1_800);
  };

  return (
    <div aria-labelledby={ariaLabelledBy} className="code" hidden={hidden} id={id} role={role}>
      <div className="code-head">
        <span>shell</span>
        <button aria-label={copyLabel} aria-live="polite" className="copy" onClick={() => void copy()} type="button">
          {copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy failed" : "Copy"}
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
