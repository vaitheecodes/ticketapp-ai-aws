/* Excerpting for ticket lists.
 *
 * The queue used to render raw concatenated ticket_text clipped by CSS
 * ellipsis, which cut mid-word ("...The customer reports h"). An excerpt
 * should end where a reader would stop: a sentence if one lands in range,
 * otherwise a whole word.
 */

export function excerpt(text, max = 140) {
  const clean = String(text || "")
    .replace(/\s+/g, " ")
    .trim();
  if (clean.length <= max) return clean;

  const window = clean.slice(0, max + 1);

  // Prefer a sentence boundary, but only if it isn't so early that the
  // excerpt stops saying anything.
  const sentenceEnd = Math.max(
    window.lastIndexOf(". "),
    window.lastIndexOf("? "),
    window.lastIndexOf("! "),
  );
  if (sentenceEnd >= max * 0.5) return clean.slice(0, sentenceEnd + 1);

  const wordEnd = window.lastIndexOf(" ");
  return `${clean.slice(0, wordEnd > 0 ? wordEnd : max).replace(/[,;:]$/, "")}…`;
}

export function firstLine(text) {
  return String(text || "").split("\n")[0].trim();
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
