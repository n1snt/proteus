import { type ReactNode, useEffect, useRef } from "react";
import { AlertTriangle, Check, X } from "lucide-react";

export function Mark() {
  return (
    <span className="mark" aria-label="Proteus">
      <i />
      <i />
      <i />
    </span>
  );
}

export function Status({ status }: { status: string }) {
  const names: Record<string, string> = {
    ready: "Ready",
    busy: "Busy",
    merged: "Merged",
    needs_attention: "Needs attention",
    queued: "Queued",
    running: "Running",
    succeeded: "Complete",
    failed: "Failed",
  };
  return (
    <span className={"status status-" + status}>
      <span />
      {names[status] || status}
    </span>
  );
}

export function Notice({
  kind,
  children,
}: {
  kind: "error" | "warning" | "success";
  children: ReactNode;
}) {
  const icon =
    kind === "success" ? <Check size={16} /> : <AlertTriangle size={16} />;
  return (
    <div
      className={"notice " + kind}
      role={kind === "error" ? "alert" : "status"}
    >
      {icon}
      <span>{children}</span>
    </div>
  );
}

export function Empty({
  icon,
  title,
  text,
}: {
  icon: ReactNode;
  title: string;
  text: string;
}) {
  return (
    <div className="empty">
      {icon}
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  );
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function Modal({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const closeRef = useRef(close);
  closeRef.current = close;

  useEffect(() => {
    trigger.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const first = dialog.current?.querySelector<HTMLElement>(
      "input:not(:disabled), select:not(:disabled), textarea:not(:disabled)",
    );
    (first || dialog.current?.querySelector<HTMLElement>("button"))?.focus();
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialog.current) return;
      const focusable = [
        ...dialog.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
        ),
      ];
      if (!focusable.length) return;
      const firstItem = focusable[0];
      const lastItem = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === firstItem) {
        event.preventDefault();
        lastItem.focus();
      }
      if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault();
        firstItem.focus();
      }
    }
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("keydown", keydown);
      trigger.current?.focus();
    };
  }, []);

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={close}>
      <section
        ref={dialog}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <h2>{title}</h2>
          <button
            className="icon-only"
            aria-label="Close dialog"
            onClick={close}
          >
            <X size={18} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
