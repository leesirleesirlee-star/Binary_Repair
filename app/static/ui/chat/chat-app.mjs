import {el} from '../shared/dom.mjs';
import {icon} from '../shared/icons.mjs';
import {label} from './chat-copy.mjs';
import {chatPreview} from './chat-stream.mjs';
import {openModel, openSearch, openSettings} from './chat-dialogs.mjs';
import {appMark, paintSidebar, sidebar} from './chat-nav.mjs';
import {bindSidebarSpring} from './chat-spring.mjs';
import {refreshStepCard} from './chat-steps.mjs';
import {control, displayTitle, renderItem, transcript} from './chat-view.mjs';
import {activityTypes, createExecution, elapsed} from './chat-workspace.mjs';

const root = document.documentElement;
const app = document.querySelector('#app');
const live = el('div', {class: 'visually-hidden', role: 'status', 'aria-atomic': 'true'});
app.after(live);

function readPreference(key, allowed, fallback) {
  try {
    const saved = localStorage.getItem('binaryrepair.chat.' + key);
    if (allowed.includes(saved)) return saved;
  } catch { /* Use the session default. */ }
  return fallback;
}

let locale = readPreference('language', ['en', 'zh-Hans'], 'en');
let theme = readPreference('theme', ['system', 'light', 'dark'], 'dark');
let model = readPreference('model', ['default', 'deepseek-flash', 'deepseek-v4-pro'], 'default');
let stopSpring = () => {};
const t = (key, count) => label(locale, key, count);
const projections = new Map();
const drafts = new Map();
let selected;
let frame = 0;
let nodes;
let selectionChanged = false;
let navStamp;

const engine = chatPreview({notify(chat, event) {
  if (chat === selected && ['run_state', 'run_end', 'assistant_end'].includes(event.type)) {
    const status = event.payload.status ?? event.payload.execution;
    if (event.type === 'assistant_end') {
      const text = chat.events.filter(item => item.type === 'assistant_delta' &&
        item.payload.message_id === event.payload.message_id).map(item => item.payload.append).join('');
      if (text) live.textContent = text;
    } else {
      live.textContent = t(status);
    }
  }
  if (nodes) requestPaint();
}});

engine.seed(locale);
selected = engine.create();
const media = matchMedia('(max-width: 720px)');
const systemTheme = matchMedia('(prefers-color-scheme: dark)');
const syncTheme = () => {
  const resolved = theme === 'system' ? (systemTheme.matches ? 'dark' : 'light') : theme;
  root.dataset.theme = resolved;
  document.querySelector('link[rel="icon"]').href = './assets/mark-' + resolved + '.svg';
};
systemTheme.addEventListener('change', syncTheme);

const currentDraft = () => drafts.get(selected.id) ?? {text: '', attachment: ''};
const setDraft = updates => drafts.set(selected.id, {...currentDraft(), ...updates});
const busy = chat => ['running', 'awaiting_user', 'cancel_requested'].includes(chat.phase);
const atEnd = () => nodes.scroll.scrollHeight - nodes.scroll.scrollTop - nodes.scroll.clientHeight < 60;
function jump() {
  nodes.scroll.scrollTop = nodes.scroll.scrollHeight;
  nodes.latest.hidden = true;
}

function requestPaint() {
  if (!frame) frame = requestAnimationFrame(() => {
    frame = 0;
    paint();
  });
}

function select(chat, focusInput = false) {
  const changed = selected !== chat;
  selected = chat;
  selectionChanged ||= changed;
  nodes.navDialog.close();
  if (changed) {
    nodes.input.value = currentDraft().text;
    nodes.rows.clear();
    nodes.log.replaceChildren();
    nodes.execution.groups.clear();
    nodes.hero.hidden = true;
    paint();
    resize();
  }
  if (focusInput) nodes.input.focus();
  else if (changed) nodes.scroll.focus({preventScroll: true});
}

function newChat() {
  if (selected.events.length === 0 && !currentDraft().text && !currentDraft().attachment) return select(selected, true);
  select(engine.create(), true);
}

function send(text) {
  if (!text.trim()) return;
  engine.send(selected, text, locale, currentDraft().attachment);
  setDraft({text: '', attachment: ''});
  nodes.input.value = '';
  resize();
  paint();
  jump();
  nodes.input.focus({preventScroll: true});
}

function resize() {
  const {input} = nodes;
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 148) + 'px';
}

function preference(key, value) {
  try { localStorage.setItem('binaryrepair.chat.' + key, value); } catch { /* Retain the selection in this tab. */ }
  if (key === 'theme') {
    theme = value;
    syncTheme();
    return;
  }
  if (key === 'model') {
    model = value;
    return;
  }
  locale = value;
  mount();
  nodes.language.focus();
}

function mount() {
  root.lang = locale;
  syncTheme();
  stopSpring();
  document.querySelector('#skip').textContent = t('skip');
  const previousScroll = nodes?.scroll.scrollTop;
  const list = el('div', {class: 'conversation-list'});
  const mobileList = el('div', {class: 'conversation-list'});
  function navArgs(list, mobile) {
    const open = callback => () => {
      if (mobile) nodes.navDialog.close();
      callback();
    };
    return {list, mobile, t, newChat, search: open(() => openSearch({t, engine, select})),
      settings: open(() => openSettings({t, locale, theme, preference})),
      model: open(() => openModel({t, model, preference})), close: () => nodes.navDialog.close()};
  }
  const nav = sidebar(navArgs(list, false));
  const navDialog = el('dialog', {class: 'mobile-nav glass', id: 'mobile-conversations', 'aria-label': t('chats')},
    sidebar(navArgs(mobileList, true)).body);
  const openNav = control(t('openNav'), () => {
    navDialog.showModal();
    openNav.setAttribute('aria-expanded', 'true');
  }, {glyph: 'sidebar', compact: true, tone: 'mobile-only', 'aria-expanded': 'false', 'aria-controls': 'mobile-conversations'});
  navDialog.addEventListener('close', () => {
    openNav.setAttribute('aria-expanded', 'false');
    if (media.matches && document.activeElement === document.body) openNav.focus();
  });
  const language = control(locale === 'en' ? '中文' : 'English',
    () => preference('language', locale === 'en' ? 'zh-Hans' : 'en'), {lang: locale === 'en' ? 'zh-Hans' : 'en'});
  const title = el('h1', {class: 'chat-title'});
  const openExecution = control(t('execution'), () => changePane(true), {glyph: 'terminal', tone: 'compact-only',
    'aria-controls': 'execution-pane'});
  const execution = createExecution(t, () => changePane(false));
  const header = el('header', {class: 'chat-header'}, openNav, title,
    el('span', {class: 'preview-label'}, el('span', {'aria-hidden': 'true'}), t('preview')), openExecution, language);
  const hero = el('section', {class: 'empty-chat'},
    appMark('hero-mark'), el('h2', {}, t('empty')),
    el('p', {}, t('help')), control(t('sample'), () => send(t('samplePrompt')), {glyph: 'arrow', tone: 'sample'}));
  const log = el('div', {class: 'transcript', role: 'log', 'aria-live': 'off', 'aria-label': t('chats')});
  const scroll = el('div', {class: 'chat-scroll', tabindex: '0', onscroll: () => {
    if (nodes) nodes.latest.hidden = atEnd();
  }}, hero, log);
  const input = el('textarea', {rows: '1', value: currentDraft().text, maxlength: '4000',
    'aria-label': t('composer'), oninput: () => {
      setDraft({text: input.value});
      resize();
      paintComposer();
    }, onkeydown: event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        send(input.value);
      }
    }});
  const file = el('input', {type: 'file', hidden: true, onchange: () => {
    setDraft({attachment: file.files[0]?.name ?? ''});
    file.value = '';
    paintComposer();
  }});
  const attached = el('div', {class: 'attached', hidden: true});
  const attach = control(t('attach'), () => file.click(), {glyph: 'plus', compact: true});
  const stop = control(t('stop'), () => engine.stop(selected), {glyph: 'stop', tone: 'stop'});
  const sendButton = control(t('send'), null, {type: 'submit', glyph: 'up', compact: true, tone: 'primary send-button'});
  const queue = control(t('queue'), null, {type: 'submit', tone: 'queue'});
  const latest = control(t('latest'), jump, {glyph: 'down', tone: 'latest', hidden: true});
  const composer = el('form', {class: 'composer', onsubmit: event => {
    event.preventDefault();
    send(input.value);
  }}, attached, input, el('div', {class: 'composer-tools'}, file, attach, el('span', {class: 'spacer'}), stop, queue, sendButton));

  const left = el('section', {class: 'chat-main glass'}, el('div', {}, header, execution.metrics), scroll,
    el('footer', {class: 'composer-wrap'}, latest, composer));
  const workspace = el('main', {id: 'conversation', tabindex: '-1', class: 'workspace'}, left, execution.panel);
  function changePane(open) {
    workspace.classList.toggle('show-execution', open);
    (open ? execution.scroll : openExecution).focus({preventScroll: true});
  }
  const rail = el('aside', {class: 'desktop-nav glass'}, nav.body);
  app.replaceChildren(el('div', {class: 'chat-shell'}, rail, workspace, navDialog));
  stopSpring = bindSidebarSpring(rail, nav.toggle);
  nodes = {rows: new Map(), list, mobileList, title, hero, log, scroll, input, latest, attach, attached, stop, sendButton, queue, language, navDialog, execution};
  navStamp = null;
  paint();
  resize();
  if (previousScroll != null) scroll.scrollTop = previousScroll;
}

function paintComposer() {
  const {input, stop, sendButton, queue, attach, attached} = nodes;
  const active = busy(selected);
  input.placeholder = t(selected.events.length ? 'followup' : 'placeholder');
  stop.hidden = !active;
  stop.disabled = selected.phase === 'cancel_requested';
  sendButton.hidden = active;
  sendButton.disabled = !input.value.trim();
  queue.hidden = !active;
  queue.disabled = !input.value.trim();
  const {attachment} = currentDraft();
  attach.hidden = selected.events.length > 0;
  if (attached.dataset.filename !== attachment) {
    attached.dataset.filename = attachment;
    attached.replaceChildren(el('span', {}, icon('file', 15), attachment), control(t('remove'), () => {
      setDraft({attachment: ''});
      paintComposer();
    }, {glyph: 'x', compact: true}));
  }
  attached.hidden = !attachment;
}

function paint() {
  const stick = selectionChanged || atEnd();
  const trace = nodes.execution.scroll;
  const stickTrace = selectionChanged || trace.scrollHeight - trace.scrollTop - trace.clientHeight < 60;
  selectionChanged = false;
  navStamp = paintSidebar({chats: engine.chats, selected, lists: [nodes.list, nodes.mobileList], navStamp, t, select});
  nodes.title.textContent = displayTitle(selected, t);
  nodes.hero.hidden = selected.events.length > 0;
  if (!projections.has(selected.id)) projections.set(selected.id, transcript());
  const items = [...projections.get(selected.id).apply(selected.events)];
  nodes.execution.update(selected, items);
  const latestApproval = items.findLast(item => item.type === 'awaiting_user' && item.decision === 'approve');
  for (const item of items) {
    const previous = nodes.rows.get(item.key);
    const flags = item.type === 'awaiting_user' && item.decision === 'approve' ? selected.phase + selected.startReady :
      item.type === 'error' ? selected.phase + selected.run : '';
    if (previous?.item === item && previous.flags === flags) continue;
    const row = renderItem(item, {t, locale, chat: selected, engine, latestApproval});
    if (!row) continue;
    row.dataset.event = item.type;
    if (previous && refreshStepCard(previous.row, row)) {
      nodes.rows.set(item.key, {row: previous.row, item, flags});
      continue;
    }
    if (previous) {
      const oldDetails = previous.row.matches('details') ? [previous.row] : [];
      oldDetails.push(...previous.row.querySelectorAll('details'));
      const newDetails = row.matches('details') ? [row] : [];
      newDetails.push(...row.querySelectorAll('details'));
      for (const [index, detail] of newDetails.entries()) detail.open = oldDetails[index]?.open ?? false;
      const focused = previous.row.contains(document.activeElement);
      previous.row.replaceWith(row);
      if (focused) (row.querySelector('summary,button') ?? nodes.input).focus({preventScroll: true});
    } else {
      if (item.type === 'plan_update') row.open = true;
      (activityTypes.has(item.type) ? nodes.execution.groups.target(item, selected.run) : nodes.log).append(row);
    }
    nodes.rows.set(item.key, {row, item, flags});
  }
  if (stick) jump();
  else nodes.latest.hidden = atEnd();
  if (stickTrace) trace.scrollTop = trace.scrollHeight;
  paintComposer();
}

media.addEventListener('change', () => {
  if (!media.matches && nodes.navDialog.open) nodes.navDialog.close();
});

document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) {
    event.preventDefault();
    openSearch({t, engine, select});
  }
});

setInterval(() => {
  nodes.execution.clock.textContent = elapsed(nodes.execution.clock);
  for (const timer of nodes.execution.log.querySelectorAll('[data-elapsed]')) {
    timer.textContent = Math.max(0, Math.floor((Date.now() - Date.parse(timer.dataset.elapsed)) / 1000)) + ' s';
  }
}, 250);

mount();
