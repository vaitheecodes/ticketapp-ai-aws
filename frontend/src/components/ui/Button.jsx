/* One button vocabulary for all three portals.
 *
 * variant: primary | secondary | ghost | danger
 * size:    sm | md
 * States: rest, hover, active, focus-visible, disabled, loading — all of
 * them, because shipping half of them is what makes a UI feel unfinished.
 */

export default function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  block = false,
  disabled = false,
  className = "",
  children,
  ...rest
}) {
  const classes = [
    "btn",
    `btn-${variant}`,
    `btn-${size}`,
    block && "btn-block",
    loading && "is-loading",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <span className="btn-spinner" aria-hidden="true" />}
      <span className="btn-label">{children}</span>
    </button>
  );
}
