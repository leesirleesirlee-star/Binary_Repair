const themeKey = 'binaryrepair.theme';
const previousKeys = ['binaryrepair.chat.theme', 'binaryrepair.ui.theme'];
const themes = ['system', 'light', 'dark'];
const systemTheme = matchMedia('(prefers-color-scheme: dark)');

function readTheme(fallback = 'system') {
  try {
    for (const key of [themeKey, ...previousKeys]) {
      const value = localStorage.getItem(key);
      if (themes.includes(value)) return value;
    }
    return 'system';
  } catch {
    return fallback;
  }
}

let theme = readTheme();
export const getTheme = () => theme;

function applyTheme() {
  const resolved = theme === 'system' ? (systemTheme.matches ? 'dark' : 'light') : theme;
  document.documentElement.dataset.theme = resolved;
  const icon = document.querySelector('link[rel="icon"]');
  if (icon) icon.href = '/ui/assets/mark-' + resolved + '.svg';
}

export function setTheme(value) {
  if (!themes.includes(value)) return;
  theme = value;
  try {
    localStorage.setItem(themeKey, theme);
  } catch { /* Keep the choice for this page. */ }
  applyTheme();
}

function syncTheme() {
  theme = readTheme(theme);
  applyTheme();
}

applyTheme();
systemTheme.addEventListener('change', applyTheme);
window.addEventListener('storage', event => {
  if (event.key === null || event.key === themeKey) syncTheme();
});
window.addEventListener('pageshow', event => {
  if (event.persisted) syncTheme();
});
