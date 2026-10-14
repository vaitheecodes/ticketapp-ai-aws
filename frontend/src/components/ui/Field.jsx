/* Field + Input + Textarea + Select.
 *
 * Every field gets a real <label htmlFor>, never a placeholder standing in
 * for one — a placeholder disappears the moment someone types, taking the
 * only description of the field with it. `useId` wires label/control/error
 * so callers can't forget.
 */

import { useId } from "react";

export function Field({ label, hint, error, children, htmlFor }) {
  return (
    <div className="field">
      {label && (
        <label className="field-label" htmlFor={htmlFor}>
          {label}
        </label>
      )}
      {children}
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : (
        hint && <p className="field-hint">{hint}</p>
      )}
    </div>
  );
}

function useFieldIds(error) {
  const id = useId();
  return { id, errorId: error ? `${id}-error` : undefined };
}

export function Input({ label, hint, error, className = "", ...rest }) {
  const { id, errorId } = useFieldIds(error);
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <input
        id={id}
        className={`control ${error ? "has-error" : ""} ${className}`.trim()}
        aria-invalid={error ? true : undefined}
        aria-describedby={errorId}
        {...rest}
      />
    </Field>
  );
}

export function Textarea({ label, hint, error, className = "", rows = 5, ...rest }) {
  const { id, errorId } = useFieldIds(error);
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <textarea
        id={id}
        rows={rows}
        className={`control control-textarea ${error ? "has-error" : ""} ${className}`.trim()}
        aria-invalid={error ? true : undefined}
        aria-describedby={errorId}
        {...rest}
      />
    </Field>
  );
}

export function Select({ label, hint, error, children, className = "", ...rest }) {
  const { id, errorId } = useFieldIds(error);
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <div className="select-wrap">
        <select
          id={id}
          className={`control control-select ${error ? "has-error" : ""} ${className}`.trim()}
          aria-invalid={error ? true : undefined}
          aria-describedby={errorId}
          {...rest}
        >
          {children}
        </select>
        <svg className="select-chevron" viewBox="0 0 16 16" aria-hidden="true">
          <path
            d="M4 6.5 8 10.5l4-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    </Field>
  );
}
