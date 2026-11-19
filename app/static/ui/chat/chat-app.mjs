import {el, isAscii} from '../shared/dom.mjs';
import {saveLanguage, takeDraft} from '../shared/handoff.mjs';
import {icon} from '../shared/icons.mjs';
import {getTheme, setTheme} from '../shared/theme.mjs';
import {label} from './chat-copy.mjs';
import {chatLive} from './chat-live.mjs';
import {chatPreview} from './chat-stream.mjs';
import {createContractWizard, guidedContractCard} from './contract-wizard.mjs';
import {openModel, openSearch, openSettings} from './chat-dialogs.mjs';
import {appMark, paintSidebar, sidebar} from './chat-nav.mjs';
import {bindSidebarSpring} from './chat-spring.mjs';
import {refreshStepCard} from './chat-steps.mjs';
import {control, displayTitle, renderItem, transcript} from './chat-view.mjs';
import {activityTypes, createExecution, elapsed} from './chat-workspace.mjs';
import {readTypedContract} from './typed-review.mjs';

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

const params = new URLSearchParams(location.search);
const previewMode = params.get('preview') === '1';

function announce(chat, event) {
  if (chat === selected && event && ['run_state', 'run_end', 'assistant_end'].includes(event.type)) {
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
}

const engine = previewMode ? chatPreview({notify: announce}) : chatLive({notify: announce});
const wizard = createContractWizard({locale: () => locale, refresh: chat => engine.refresh(chat), repaint: requestPaint});
const requestedJob = params.get('job');
Promise.resolve(engine.seed(locale)).then(() => {
  if (!requestedJob) return;
  const target = [...engine.chats.values()].find(chat => chat.jobId === requestedJob);
  if (target) select(target);
});
selected = engine.create();
if (!requestedJob && !previewMode) {
  const text = takeDraft();
  if (text) drafts.set(selected.id, {text, attachment: '', file: null});
}
const media = matchMedia('(max-width: 720px)');

const draftFor = chat => drafts.get(chat.id) ?? {text: '', attachment: '', file: null};
const currentDraft = () => draftFor(selected);
const setDraft = updates => drafts.set(selected.id, {...currentDraft(), ...updates});
const busy = chat => ['running', 'awaiting_user', 'cancel_requested', 'queued', 'waiting_for_executor'].includes(chat.phase);
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
  if (changed) {
    wizard.dialog.close();
    engine.deactivate?.();
  }
  selected = chat;
  selectionChanged ||= changed;
  nodes.navDialog.close();
  if (changed) {
    nodes.input.value = currentDraft().text;
    nodes.composerFeedback.textContent = '';
    nodes.rows.clear();
    nodes.log.replaceChildren();
    nodes.execution.groups.clear();
    nodes.hero.hidden = true;
    paint();
    resize();
    void engine.activate?.(chat);
  }
  if (focusInput) nodes.input.focus();
  else if (changed) nodes.scroll.focus({preventScroll: true});
}

function newChat() {
  const draft = currentDraft();
  if (!busy(selected) && selected.events.length === 0 && !draft.text && !draft.attachment && !draft.typedContract) {
    return select(selected, true);
  }
  select(engine.create(), true);
}

async function send(text) {
  if (!text.trim() || selected.contractSelection || (engine.live && busy(selected))) return;
  const chat = selected;
  const {attachment, file, typedContract} = currentDraft();
  if (engine.live && !selected.jobId && (!file || !isAscii(text.trim()))) {
    nodes.composerFeedback.textContent = t(file ? 'asciiDefect' : 'needFile');
    return;
  }
  nodes.composerFeedback.textContent = '';
  const creating = engine.live && !chat.jobId;
  const result = engine.send(chat, text, locale, attachment, file, typedContract);
  if (creating && !await result) {
    if (selected === chat && chat.creationError) nodes.composerFeedback.textContent = chat.creationError;
    return;
  }
  drafts.delete(chat.id);
  if (selected !== chat) return;
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
  if (key === 'theme') {
    setTheme(value);
    return;
  }
  try { localStorage.setItem('binaryrepair.chat.' + key, value); } catch { /* Retain the selection in this tab. */ }
  if (key === 'model') {
    model = value;
    return;
  }
  locale = value;
  saveLanguage(value);
  engine.setLocale?.(value);
  mount();
  nodes.language.focus();
}

function mount() {
  root.lang = locale;
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
    return {list, mobile, t, newChat: open(newChat), conversation: open(() => nodes.input.focus()), search: open(() => openSearch({t, engine, select})),
      settings: open(() => openSettings({t, locale, theme: getTheme(), preference})),
      model: open(() => openModel({t, model, preference, preview: previewMode})),
      archive: open(() => { location.href = '/ui/'; }),
      close: () => nodes.navDialog.close()};
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
  const language = control(locale === 'en' ? '中文' : 'EN',
    () => preference('language', locale === 'en' ? 'zh-Hans' : 'en'), {lang: locale === 'en' ? 'zh-Hans' : 'en'});
  const title = el('h1', {class: 'chat-title'});
  const openExecution = control(t('execution'), () => changePane(true), {glyph: 'terminal', tone: 'compact-only',
    'aria-controls': 'execution-pane'});
  const execution = createExecution(t, () => changePane(false));
  const sessionLabel = el('span');
  const header = el('header', {class: 'chat-header'}, openNav, title,
    el('span', {class: 'preview-label'}, el('span', {'aria-hidden': 'true'}), sessionLabel), openExecution, language);
  const heroAttach = engine.live
    ? control(t('attachBinary'), () => file.click(), {glyph: 'file', tone: 'primary hero-attach'})
    : null;
  const hero = el('section', {class: 'empty-chat'},
    appMark('hero-mark'), el('h2', {}, t('empty')),
    el('p', {}, t(engine.live ? 'helpLive' : 'help')),
    heroAttach ?? control(t('sample'), () => send(t('samplePrompt')), {glyph: 'arrow', tone: 'sample'}));
  const log = el('div', {class: 'transcript', role: 'log', 'aria-live': 'off', 'aria-label': t('chats')});
  const contractSlot = el('div', {class: 'transcript guided-contract-slot', hidden: true});
  const scroll = el('div', {class: 'chat-scroll', tabindex: '0', onscroll: () => {
    if (nodes) nodes.latest.hidden = atEnd();
  }}, hero, log, contractSlot);
  const composerFeedback = el('p', {class: 'composer-feedback', role: 'status'});
  const input = el('textarea', {rows: '1', value: currentDraft().text, maxlength: '4000',
    'aria-label': t('composer'), oninput: () => {
      setDraft({text: input.value});
      composerFeedback.textContent = '';
      resize();
      paintComposer();
    }, onkeydown: event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        input.form.requestSubmit();
      }
    }});
  const file = el('input', {type: 'file', hidden: true, onchange: () => {
    const picked = file.files[0] ?? null;
    setDraft({attachment: picked?.name ?? '', file: picked});
    file.value = '';
    composerFeedback.textContent = '';
    paintComposer();
    if (picked) input.focus({preventScroll: true});
  }});
  const contractFile = el('input', {type: 'file', accept: '.json,application/json', hidden: true, onchange: async () => {
    const source = contractFile.files[0];
    contractFile.value = '';
    if (!source) return;
    const chat = selected;
    const selection = Symbol();
    chat.contractSelection = selection;
    paintComposer();
    try {
      const typedContract = await readTypedContract(source);
      if (chat.contractSelection !== selection || chat.jobId || busy(chat)) return;
      drafts.set(chat.id, {...draftFor(chat), typedContract});
      if (selected === chat) {
        nodes.composerFeedback.textContent = '';
        nodes.input.focus({preventScroll: true});
      }
    } catch (error) {
      if (selected === chat && chat.contractSelection === selection) {
        nodes.composerFeedback.textContent = t(error.code === 'payload_too_large' ? 'contractFileTooLarge' : 'invalidContractFile');
      }
    } finally {
      if (chat.contractSelection === selection) {
        chat.contractSelection = null;
        if (selected === chat) paintComposer();
      }
    }
  }});
  const attached = el('div', {class: 'attached', hidden: true});
  const attachedContract = el('div', {class: 'attached', hidden: true});
  const attach = control(t(engine.live ? 'attachBinary' : 'attach'), () => file.click(), {glyph: 'plus'});
  const attachContract = control(t('attachContract'), () => contractFile.click(), {glyph: 'file'});
  const stop = control(t('stop'), () => engine.stop(selected), {glyph: 'stop', tone: 'stop'});
  const sendButton = control(t('send'), null, {type: 'submit', glyph: 'up', compact: true, tone: 'primary send-button'});
  const queue = control(t('queue'), null, {type: 'submit', tone: 'queue'});
  const latest = control(t('latest'), jump, {glyph: 'down', tone: 'latest', hidden: true});
  const composer = el('form', {class: 'composer', onsubmit: event => {
    event.preventDefault();
    send(input.value);
  }}, attached, attachedContract, input, composerFeedback, el('div', {class: 'composer-tools'},
    file, attach, contractFile, attachContract, el('span', {class: 'spacer'}), stop, queue, sendButton));

  const left = el('section', {class: 'chat-main glass'}, el('div', {}, header, execution.metrics), scroll,
    el('footer', {class: 'composer-wrap'}, latest, composer));
  const workspace = el('main', {id: 'conversation', tabindex: '-1', class: 'workspace'}, left, execution.panel);
  function changePane(open) {
    workspace.classList.toggle('show-execution', open);
    (open ? execution.scroll : openExecution).focus({preventScroll: true});
  }
  const rail = el('aside', {class: 'desktop-nav glass'}, nav.body);
  app.replaceChildren(el('div', {class: 'chat-shell'}, rail, workspace, navDialog, wizard.dialog));
  stopSpring = bindSidebarSpring(rail, nav.toggle);
  nodes = {rows: new Map(), list, mobileList, title, hero, heroAttach, log, scroll, input, composerFeedback,
    latest, attach, attached, attachContract, attachedContract, stop, sendButton, queue, language,
    navDialog, execution, contractSlot, sessionLabel};
  nodes.workspace = workspace;
  navStamp = null;
  paint();
  resize();
  if (previousScroll != null) scroll.scrollTop = previousScroll;
}

function paintComposer() {
  const {input, stop, sendButton, queue, hero, heroAttach, attach, attached, attachContract, attachedContract} = nodes;
  const active = busy(selected);
  const live = Boolean(selected.live);
  const {attachment, file, typedContract} = currentDraft();
  input.placeholder = t(selected.phase === 'awaiting_user' ? 'approvalTurn' :
    active ? 'waitingTurn' : selected.events.length ? 'followup' : 'placeholder');
  input.readOnly = active && live;
  stop.hidden = !active || !selected.run;
  stop.disabled = selected.cancelSubmitting || selected.phase === 'cancel_requested';
  sendButton.hidden = active;
  sendButton.disabled = !input.value.trim() || Boolean(selected.contractSelection) || (live && !selected.jobId && !file);
  queue.hidden = !active || live;
  queue.disabled = !input.value.trim();
  const showHeroAttach = live && !hero.hidden && !file && !selected.jobId;
  if (heroAttach) heroAttach.hidden = !showHeroAttach;
  if (heroAttach) heroAttach.disabled = active;
  attach.hidden = live ? showHeroAttach || Boolean(selected.jobId) : selected.events.length > 0;
  attach.disabled = live && active;
  attachContract.hidden = !live || Boolean(selected.jobId);
  attachContract.disabled = active;
  if (attached.dataset.filename !== attachment) {
    attached.dataset.filename = attachment;
    attached.replaceChildren(el('span', {}, icon('file', 15), attachment), control(t('remove'), () => {
      if (selected.live && busy(selected)) return;
      setDraft({attachment: '', file: null});
      paintComposer();
      (heroAttach && !heroAttach.hidden ? heroAttach : input).focus();
    }, {glyph: 'x', compact: true}));
  }
  attached.hidden = !attachment;
  const contractName = typedContract?.name ?? '';
  if (attachedContract.dataset.filename !== contractName) {
    attachedContract.dataset.filename = contractName;
    attachedContract.replaceChildren(el('span', {}, icon('file', 15), t('attachContract') + ' · ' + contractName),
      control(t('removeContract'), () => {
        if (busy(selected)) return;
        setDraft({typedContract: undefined});
        paintComposer();
        attachContract.focus();
      }, {glyph: 'x', compact: true}));
  }
  attachedContract.hidden = !contractName;
  for (const item of [attached, attachedContract]) {
    const remove = item.querySelector('button');
    if (remove) remove.disabled = live && active;
  }
}

function paint() {
  const stick = selectionChanged || atEnd();
  const trace = nodes.execution.scroll;
  const stickTrace = selectionChanged || trace.scrollHeight - trace.scrollTop - trace.clientHeight < 60;
  selectionChanged = false;
  navStamp = paintSidebar({chats: engine.chats, selected, lists: [nodes.list, nodes.mobileList], navStamp, t, select});
  nodes.title.textContent = displayTitle(selected, t);
  nodes.sessionLabel.textContent = t(engine.live && !selected.demo ? 'liveLabel' : 'preview');
  nodes.hero.hidden = selected.events.length > 0;
  const beforeRun = selected.live ? !selected.execution?.run_id : selected.kind !== 'repair';
  nodes.workspace.classList.toggle('before-run', beforeRun);
  if (nodes.workspace.dataset.chat !== selected.id || !beforeRun) {
    nodes.workspace.classList.remove('inspect-before-run');
    nodes.execution.inspect.setAttribute('aria-expanded', 'false');
    nodes.workspace.dataset.chat = selected.id;
  }
  if (!projections.has(selected.id)) projections.set(selected.id, transcript());
  const items = [...projections.get(selected.id).apply(selected.events)];
  nodes.execution.update(selected, items);
  const latestApproval = items.findLast(item => item.type === 'awaiting_user' && item.decision === 'approve');
  for (const item of items) {
    if ((selected.technicalSetup || selected.typedReview) && item.type === 'awaiting_user') continue;
    const previous = nodes.rows.get(item.key);
    const flags = item.type === 'awaiting_user' && item.decision === 'approve' ? selected.phase + selected.startReady :
      item.type === 'error' ? selected.phase + selected.run :
      item.type === 'user_message' ? JSON.stringify(item.payload.attachment_ids.map(id => selected.attachmentNames.get(id))) : '';
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
  const slot = nodes.contractSlot;
  const signature = selected.technicalSetup || selected.typedReview ? JSON.stringify([
    selected.jobId, selected.contract?.revision, selected.contract?.approved,
    selected.contract?.review_binding_digest, selected.typedReview, selected.attachingContract,
    selected.typedAttachmentError, selected.reviewRefreshRequired, selected.reviewRefreshPending,
    selected.starting, selected.startAcknowledged,
    selected.execution?.run_id, selected.execution?.terminal, selected.phase, wizard.hasAnswers(selected),
  ]) : '';
  if (slot.dataset.state !== signature) {
    const card = guidedContractCard(selected, {t, locale, engine, wizard});
    slot.replaceChildren(...(card ? [card] : []));
    slot.hidden = !card;
    slot.dataset.state = signature;
  }
  if (selected.openContract && selected.technicalSetup && !wizard.dialog.open) {
    selected.openContract = false;
    wizard.show(selected, 'answers');
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
void engine.activate?.(selected);
