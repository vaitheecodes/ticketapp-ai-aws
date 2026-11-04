/* Color theme: "system" (follow the OS: no data-theme attribute), or a
 * forced "light" / "dark" on <html> (styles/tokens.css). Saved in
 * localStorage; index.html applies it before first paint. */

const KEY = "theme";

export function getTheme() {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function setTheme(theme) {
  const root = document.documentElement;
  try {
    if (theme === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    // Storage blocked: the choice still applies to this page.
  }
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
}
