// Welcome page: a single language switch that also seeds the two app pages.
const root = document.documentElement;
const button = document.querySelector('#language');
const KEY = 'binaryrepair.welcome.language';
const SUPPORTED = new Set(['zh-Hans', 'en']);

function read() {
  try {
    const saved = localStorage.getItem(KEY) ||
      localStorage.getItem('binaryrepair.chat.language') ||
      localStorage.getItem('binaryrepair.ui.language');
    if (SUPPORTED.has(saved)) return saved;
  } catch {
    // Browser storage can be unavailable; fall back to the document language.
  }
  return root.lang === 'en' ? 'en' : 'zh-Hans';
}

function apply(language) {
  root.lang = language;
  for (const node of document.querySelectorAll('[data-zh][data-en]')) {
    node.textContent = language === 'en' ? node.dataset.en : node.dataset.zh;
  }
  button.textContent = language === 'en' ? '中文' : 'EN';
  button.setAttribute('aria-label', language === 'en' ? '切换到中文' : 'Switch to English');
  try {
    localStorage.setItem(KEY, language);
    localStorage.setItem('binaryrepair.chat.language', language);
    localStorage.setItem('binaryrepair.ui.language', language);
  } catch {
    // The visible language still changes for this page.
  }
}

let language = read();
apply(language);
try {
  const theme = localStorage.getItem('binaryrepair.chat.theme') ||
    localStorage.getItem('binaryrepair.ui.theme');
  if (theme === 'light' || theme === 'dark') root.dataset.theme = theme;
} catch {
  // Keep the system theme.
}
button.addEventListener('click', () => {
  language = language === 'en' ? 'zh-Hans' : 'en';
  apply(language);
  button.focus();
});
