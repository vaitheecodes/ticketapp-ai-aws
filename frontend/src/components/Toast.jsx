import { useEffect } from "react";

/* One transient confirmation at a time. role="status" so screen readers
 * announce it without stealing focus; dismisses itself after a few
 * seconds and can be closed early. */
export default function Toast({ toast, onDismiss }) {
  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(onDismiss, toast.tone === "error" ? 8000 : 4500);
    return () => clearTimeout(timer);
  }, [toast, onDismiss]);

  return (
    <div className="toast-region" role="status" aria-live="polite">
      {toast && (
        <div className={`toast toast-${toast.tone || "ok"}`}>
          <span>{toast.message}</span>
          <button type="button" className="toast-close" onClick={onDismiss} aria-label="Dismiss">
            <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}
