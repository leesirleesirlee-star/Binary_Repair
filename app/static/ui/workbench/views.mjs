import {badge, button, date, el, isAscii, notice, taskHref} from '../shared/dom.mjs';
import {icon} from '../shared/icons.mjs';

export function shell(ctx) {
  const {t, language, api, jobs, id, create, settings, switchLanguage, menuOpen, toggleMenu, search} = ctx;
  const current = active => active ? 'page' : null;
  const rail = el('aside', {class: 'rail', id: 'task-navigation'},
    el('div', {class: 'rail-top'},
      el('a', {class: 'brand', href: '#tasks'}, el('span', {class: 'brand-mark'}, icon('mark', 21)), 'BinaryRepair'),
      api.writable && id ? button(t('create'), create, {glyph: 'plus', className: 'primary'}) : null,
      el('nav', {'aria-label': t('workspace')}, el('a', {class: 'nav-link', href: '#tasks', 'aria-current': current(!id)},
        icon('tasks', 16), t('tasks')))),
    jobs.length ? el('nav', {class: 'recent', 'aria-label': t('recent')},
      el('p', {class: 'rail-label'}, t('recent')),
      jobs.slice(0, 6).map(job => el('a', {class: 'nav-link', href: taskHref(job.id), title: job.defect,
        'aria-current': current(id === job.id)}, el('span', {}, job.defect || job.id)))) : null,
    el('div', {class: 'rail-footer'}, button(t('settings'), settings, {glyph: 'settings', className: 'subtle'})));
  const mobile = button(null, toggleMenu, {glyph: 'menu', className: 'icon subtle mobile-toggle',
    'aria-label': t('collapse'), 'aria-expanded': String(menuOpen), 'aria-controls': 'task-navigation'});
  const top = el('header', {class: 'topbar'},
    el('div', {class: 'top-left'}, mobile, id ?
      el('a', {class: 'button subtle', href: '#tasks'}, icon('back', 16), t('tasks')) :
      el('span', {class: 'muted'}, t('workspace'))),
    el('div', {class: 'top-right'},
      button(null, search, {glyph: 'search', className: 'icon subtle', 'aria-label': t('search'), title: t('search') + ' (Ctrl+K)'}),
      button(language === 'en' ? '中文' : 'English', switchLanguage, {className: 'subtle',
        lang: language === 'en' ? 'zh-Hans' : 'en'}),
      button(null, settings, {glyph: 'settings', className: 'icon subtle mobile-toggle', 'aria-label': t('settings')})));
  const main = el('main', {id: 'main', tabindex: '-1'}, el('div', {class: 'page'},
    api.demo || ctx.job?.demo ? notice(t('demo'), 'demo') : null,
    ctx.content));
  return el('div', {class: 'shell' + (menuOpen ? ' nav-open' : '')}, rail, el('div', {}, top, main));
}

const inProgress = new Set(['queued', 'screening', 'repair', 'verification', 'running', 'waiting_for_executor']);
const finished = new Set(['done', 'completed', 'failed', 'cancelled']);
const phase = job => job.execution?.status === 'waiting_for_executor' ? job.execution.status : job.phase;
const listFilter = (jobs, filter, text) => jobs.filter(job =>
  (filter === 'all' || (filter === 'active' ? inProgress : finished).has(job.phase)) &&
  (job.id + ' ' + (job.defect ?? '')).toLowerCase().includes(text.toLowerCase().trim()));

export function tasksView(ctx) {
  const {t, language, jobs, api, listError, loading, create, filter, query, setFilter, setQuery, reload} = ctx;
  if (loading) return el('p', {role: 'status'}, t('loading'));
  if (listError) {
    const preview = new URL(location.href);
    preview.search = '?preview=1';
    preview.hash = '';
    return el('div', {class: 'page'}, el('h1', {}, t('tasks')), notice(t(listError), 'error'),
      el('div', {class: 'actions'}, button(t('retry'), reload),
        el('a', {class: 'button', href: preview.pathname + preview.search}, t('previewLink'))));
  }

  const results = el('div', {class: 'card', id: 'task-results'});
  const filterButtons = ['all', 'active', 'finished'].map(value => button(t(value), event => {
    setFilter(value);
    for (const item of filterButtons) item.setAttribute('aria-pressed', String(item === event.currentTarget));
    update();
  }, {class: 'toggle', 'aria-pressed': String(value === filter)}));
  const input = el('input', {type: 'search', id: 'task-filter', value: query, 'aria-label': t('filterLabel'),
    placeholder: t('search'), oninput: event => { setQuery(event.currentTarget.value); update(); }});
  const update = () => {
    const visible = listFilter(jobs, ctx.getFilter(), input.value);
    results.replaceChildren(
      visible.length ? el('div', {class: 'table-head'}, el('span', {}, t('description')),
        el('span', {}, t('status')), el('span', {}, t('updated')), el('span')) :
        el('div', {class: 'empty'}, el('h2', {}, t(jobs.length ? 'noMatches' : 'empty')),
          jobs.length ? null : el('p', {class: 'muted'}, t('emptyHelp'))),
      ...visible.map(job => el('a', {class: 'task-row', href: taskHref(job.id)}, el('span', {class: 'task-title'},
        job.defect || job.id, el('small', {class: 'mono'}, job.id)), badge(phase(job), t),
        date(job.updated_at ?? job.created_at, language), el('span', {class: 'arrow'}, icon('chevron', 16)))));
  };
  update();
  return el('div', {class: 'page'},
    el('div', {class: 'section-head'}, el('h1', {}, t('tasks')),
      api.writable ? button(t('create'), create, {glyph: 'plus', className: 'primary'}) : null),
    el('div', {class: 'controls'},
      el('div', {class: 'segments', role: 'group', 'aria-label': t('filterLabel')}, filterButtons),
      el('div', {class: 'search'}, icon('search', 15), input)),
    results);
}

function dialog(title, t, ...children) {
  document.querySelector('dialog[open]')?.close();
  const opener = document.activeElement;
  const modal = el('dialog', {'aria-labelledby': 'dialog-title'},
    el('div', {class: 'section-head'}, el('h2', {id: 'dialog-title'}, title),
      button(null, () => modal.close(), {glyph: 'x', className: 'icon subtle', 'aria-label': t('close')})),
    ...children);
  modal.addEventListener('close', () => {
    modal.remove();
    if (opener?.isConnected) opener.focus();
  });
  document.body.append(modal);
  modal.showModal();
  return modal;
}

export function createDialog({t, api, created}) {
  const defect = el('textarea', {name: 'defect', rows: 4, required: true, autofocus: true, placeholder: t('defectPlaceholder'),
    oninput: event => event.currentTarget.setCustomValidity('')});
  const file = el('input', {name: 'binary', type: 'file', required: true,
    onchange: event => event.currentTarget.setCustomValidity('')});
  const feedback = el('p', {class: 'stat', role: 'status'}, t('demo'));
  const submit = button(t('createDraft'), null, {className: 'primary', type: 'submit'});
  const form = el('form', {onsubmit: async event => {
    event.preventDefault();
    const description = defect.value.trim();
    const binary = file.files[0];
    defect.setCustomValidity(!description ? t('required') : !isAscii(description) ? t('ascii') : '');
    file.setCustomValidity(!binary?.size ? t('fileRequired') : !isAscii(binary.name) ? t('ascii') : '');
    if (!form.reportValidity()) return;
    submit.disabled = true;
    try {
      const id = await api.create({defect: description});
      modal.close();
      created(id);
    } catch {
      feedback.textContent = t('unavailable');
      submit.disabled = false;
    }
  }}, el('label', {class: 'label'}, t('defect'), defect),
    el('label', {class: 'label'}, t('binary'), file), feedback, el('div', {class: 'actions'}, submit));
  const modal = dialog(t('create'), t, form);
  defect.focus();
}

export function settingsDialog({t, preferences, setPreference, provider}) {
  const select = (key, choices) => {
    const input = el('select', {onchange: event => setPreference(key, event.currentTarget.value)}, choices.map(([value, title]) =>
      el('option', {value}, title)));
    input.value = preferences[key];
    return input;
  };
  dialog(t('settings'), t, el('div', {class: 'stack'},
    el('label', {class: 'label'}, t('language'), select('language', [['en', 'English'], ['zh-Hans', '简体中文']])),
    el('label', {class: 'label'}, t('theme'), select('theme', ['system', 'light', 'dark'].map(value => [value, t(value)]))),
    el('label', {class: 'label'}, t('density'), select('density', ['comfortable', 'compact'].map(value => [value, t(value)]))),
    el('div', {class: 'item'}, t('provider'), t(provider === null ? 'providerUnknown' : provider ? 'configured' : 'unconfigured'))));
}

export function searchDialog({t, jobs, navigate}) {
  const results = el('div', {class: 'stack'});
  function update(text) {
    const matches = listFilter(jobs, 'all', text).slice(0, 8);
    results.replaceChildren(...matches.map(job => button(job.defect || job.id, () => {
      modal.close();
      navigate(taskHref(job.id));
    }, {className: 'subtle wrap'})), matches.length ? el('span', {hidden: true}) : el('p', {class: 'muted'}, t('noMatches')));
  }
  const search = el('input', {type: 'search', 'aria-label': t('search'), placeholder: t('search'),
    oninput: event => update(event.currentTarget.value)});
  const modal = dialog(t('search'), t, el('div', {class: 'stack'}, search, results));
  update('');
  search.focus();
}
