/* Multi-file attachment input with drag-and-drop and removable chips.
 *
 * Mirrors the backend's limits (backend/main.py upload_attachments) so
 * the customer hears about a bad file before submitting, not after the
 * ticket already exists: max 5 files, 10 MB each, png/jpg/pdf/txt/log.
 * The backend re-checks everything, including the file contents. */

import { useId, useRef, useState } from "react";
import { formatBytes } from "../utils/text";

const MAX_FILES = 5;
const MAX_BYTES = 10 * 1024 * 1024;
const ALLOWED = [".png", ".jpg", ".jpeg", ".pdf", ".txt", ".log"];

function extensionOf(name) {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot).toLowerCase();
}

export default function AttachmentPicker({ files, onChange, disabled = false, compact = false }) {
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);
  const hintId = useId();

  function addFiles(list) {
    const incoming = Array.from(list);
    const problems = [];
    const accepted = [];
    for (const file of incoming) {
      if (!ALLOWED.includes(extensionOf(file.name))) {
        problems.push(`${file.name}: only PNG, JPG, PDF, TXT and LOG files`);
      } else if (file.size > MAX_BYTES) {
        problems.push(`${file.name} is larger than 10 MB`);
      } else if (file.size === 0) {
        problems.push(`${file.name} is empty`);
      } else if (files.some((f) => f.name === file.name && f.size === file.size)) {
        // Same file dropped twice: ignore quietly.
      } else {
        accepted.push(file);
      }
    }
    const room = MAX_FILES - files.length;
    if (accepted.length > room) {
      problems.push(`You can attach up to ${MAX_FILES} files`);
      accepted.length = Math.max(room, 0);
    }
    setError(problems.join(". "));
    if (accepted.length) onChange([...files, ...accepted]);
  }

  function remove(index) {
    setError("");
    onChange(files.filter((_, i) => i !== index));
  }

  return (
    <div className={`attach ${compact ? "attach-compact" : ""}`}>
      {!compact && (
        <div
          className={`attach-drop ${dragging ? "is-dragging" : ""} ${disabled ? "is-disabled" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            if (!disabled) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (!disabled) addFiles(e.dataTransfer.files);
          }}
        >
          <PaperclipIcon />
          <span>
            Drag files here or{" "}
            <button
              type="button"
              className="link-button attach-browse"
              onClick={() => inputRef.current?.click()}
              disabled={disabled}
              aria-describedby={hintId}
            >
              browse
            </button>
          </span>
          <span className="attach-hint" id={hintId}>
            Up to {MAX_FILES} files · PNG, JPG, PDF, TXT, LOG · 10 MB each
          </span>
        </div>
      )}
      {compact && (
        <button
          type="button"
          className="attach-button"
          onClick={() => inputRef.current?.click()}
          disabled={disabled || files.length >= MAX_FILES}
          aria-describedby={hintId}
        >
          <PaperclipIcon />
          Attach
          <span className="sr-only" id={hintId}>
            Up to {MAX_FILES} files: PNG, JPG, PDF, TXT or LOG, 10 MB each
          </span>
        </button>
      )}
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ALLOWED.join(",")}
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => {
          addFiles(e.target.files);
          e.target.value = "";
        }}
      />
      {files.length > 0 && (
        <ul className="file-chips" aria-label="Attached files">
          {files.map((file, i) => (
            <li key={`${file.name}-${file.size}`} className="file-chip">
              <span className="file-chip-name">{file.name}</span>
              <span className="file-chip-size">{formatBytes(file.size)}</span>
              <button
                type="button"
                className="file-chip-remove"
                onClick={() => remove(i)}
                disabled={disabled}
                aria-label={`Remove ${file.name}`}
              >
                <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
                  <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function PaperclipIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" className="attach-icon">
      <path
        d="M10.5 4.5 5.8 9.2a1.4 1.4 0 0 0 2 2l5-5a2.6 2.6 0 0 0-3.7-3.7l-5.1 5.1a3.8 3.8 0 0 0 5.4 5.4L13.5 9"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
