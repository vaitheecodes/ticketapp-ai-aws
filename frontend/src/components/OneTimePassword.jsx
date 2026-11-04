import { useEffect, useId, useRef, useState } from "react";

/* The temporary password of a new account or a reset, shown once. The
 * server returns it in that one response and keeps only a hash; when this
 * dialog closes the value is dropped from the page too (onClose clears
 * it). A modal <dialog> so it isn't missed and Escape closes it. */
export default function OneTimePassword({ reveal, onClose }) {
  const dialogRef = useRef(null);
  const [copied, setCopied] = useState(false);
  const titleId = useId();
  const fieldId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (reveal && !dialog.open) {
      setCopied(false);
      dialog.showModal();
    } else if (!reveal && dialog.open) {
      dialog.close();
    }
  }, [reveal]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(reveal.password);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <dialog ref={dialogRef} className="dialog" aria-labelledby={titleId} onClose={onClose}>
      {reveal && (
        <div className="dialog-body">
          <h2 id={titleId} className="dialog-title">
            {reveal.title}
          </h2>
          <p>
            Give this temporary password to <strong>{reveal.name}</strong> ({reveal.email}). They must change
            it when they first sign in.
          </p>
          <label className="field-label" htmlFor={fieldId}>
            Temporary password
          </label>
          <div className="input-with-action">
            <input id={fieldId} className="mono" type="text" readOnly value={reveal.password}
                   onFocus={(e) => e.target.select()} />
            <button type="button" onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <p className="pending-text">It won't be shown again. If it's lost, reset the password.</p>
          <div className="row-actions dialog-actions">
            <button type="button" className="btn-primary" onClick={() => dialogRef.current?.close()}>
              Done
            </button>
          </div>
        </div>
      )}
    </dialog>
  );
}
