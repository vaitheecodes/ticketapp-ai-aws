/* "Dana Smith" -> "DS"; one word -> its first letter. */
export function initialsOf(name = "") {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return ((parts[0][0] || "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

/* The chat bot's name in the UI (the backend keeps its own sender name). */
export const BOT_NAME = "Support Bot";
