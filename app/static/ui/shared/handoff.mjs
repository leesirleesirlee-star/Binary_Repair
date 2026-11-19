// Store a draft only in this tab and consume it once, without putting it in a URL.
const draftKey = 'binaryrepair.welcome.draft';
export function saveDraft(text) {
  if (text) sessionStorage.setItem(draftKey, text);
  else sessionStorage.removeItem(draftKey);
}

export function takeDraft() {
  try {
    const draft = sessionStorage.getItem(draftKey) || '';
    sessionStorage.removeItem(draftKey);
    return draft;
  } catch {
    return '';
  }
}

export function saveLanguage(language) {
  try {
    for (const page of ['welcome', 'chat', 'ui']) {
      localStorage.setItem('binaryrepair.' + page + '.language', language);
    }
  } catch { /* The active page still uses the selected language. */ }
}
