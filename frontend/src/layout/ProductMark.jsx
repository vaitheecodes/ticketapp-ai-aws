/* The product mark: a ring with a heartbeat line through it (a monitor
 * that answers), in the accent. Drawn for this product, not a letter. */
export default function ProductMark({ className = "size-6" }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" fill="none">
      <rect x="1.5" y="1.5" width="21" height="21" rx="6" className="fill-primary" />
      <path
        d="M5 12.5h3.2l1.6-3.6 2.6 7.2 1.9-5 1.1 1.4H19"
        className="stroke-primary-fg"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
