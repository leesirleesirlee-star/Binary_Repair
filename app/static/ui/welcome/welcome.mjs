import {saveDraft, saveLanguage} from '../shared/handoff.mjs';
import '../shared/theme.mjs';

const form = document.querySelector('.composer-card');
const draft = document.querySelector('#draft');
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
  draft.placeholder = language === 'en' ? 'Describe the defect…' : '描述缺陷…';
  draft.setAttribute('aria-label', draft.placeholder);
  saveLanguage(language);
}

let language = read();
apply(language);
button.addEventListener('click', () => {
  language = language === 'en' ? 'zh-Hans' : 'en';
  apply(language);
  button.focus();
});

form.addEventListener('submit', event => {
  event.preventDefault();
  try {
    saveDraft(draft.value);
  } catch {
    if (draft.value) {
      const error = document.querySelector('#draft-error');
      error.textContent = language === 'en'
        ? 'Copy your draft before continuing. Browser storage is unavailable.'
        : '浏览器无法暂存草稿，请先复制内容，再清空输入框进入对话。';
      error.hidden = false;
      return;
    }
  }
  location.assign(form.action);
});
draft.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    form.requestSubmit();
  }
});
