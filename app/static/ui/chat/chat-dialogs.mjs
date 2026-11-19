import {el} from '../shared/dom.mjs';
import {live} from '../workbench/api.mjs';
import {control, displayTitle} from './chat-view.mjs';

function dialog(title, content, t) {
  const trigger = document.activeElement;
  const popup = el('dialog', {class: 'popup', 'aria-label': title},
    el('header', {}, el('h2', {}, title), control(t('close'), () => popup.close(), {glyph: 'x', compact: true})), content);
  popup.addEventListener('close', () => {
    popup.remove();
    if (trigger?.isConnected) trigger.focus();
  });
  document.body.append(popup);
  popup.showModal();
  return popup;
}

export function openSettings({t, locale, theme, preference}) {
  function options(key, choices, selectedValue) {
    return el('fieldset', {}, el('legend', {}, t(key)), el('div', {class: 'segments'}, choices.map(([value, title]) =>
      el('label', {}, el('input', {type: 'radio', name: key, value, checked: value === selectedValue,
        onchange: () => {
          if (key === 'language') popup.close();
          preference(key, value);
        }}), el('span', {}, title)))));
  }
  const popup = dialog(t('settings'), el('div', {class: 'settings'},
    options('language', [['en', 'English'], ['zh-Hans', '简体中文']], locale),
    options('theme', ['system', 'light', 'dark'].map(value => [value, t(value)]), theme)), t);
}

export function openSearch({t, engine, select}) {
  const matches = el('div', {class: 'search-results'});
  const input = el('input', {type: 'search', placeholder: t('search'), 'aria-label': t('search'),
    oninput: () => update()});
  function update() {
    const query = input.value.trim().toLowerCase();
    const results = [...engine.chats.values()].reverse().filter(chat => chat.events.length &&
      displayTitle(chat, t).toLowerCase().includes(query));
    matches.replaceChildren(...results.map(chat => control(displayTitle(chat, t), () => {
      popup.close();
      select(chat);
    }, {glyph: 'chat'})));
    if (!results.length) matches.append(el('p', {class: 'muted'}, t('noMatch')));
  }
  const popup = dialog(t('search'), el('div', {}, input, matches), t);
  update();
  input.focus();
}

export function openModel({t, model, preference, preview}) {
  const ids = ['default', 'deepseek-flash', 'deepseek-v4-pro'];
  const select = el('select', {id: 'preferred-model', onchange: event => preference('model', event.currentTarget.value)},
    ids.map(id => el('option', {value: id}, id === 'default' ? t('managedModel') : id)));
  select.value = model;
  const key = el('input', {id: 'model-api-key', type: 'password', required: true,
    autocomplete: 'off', spellcheck: 'false', autocapitalize: 'none', disabled: preview,
    'aria-describedby': 'model-key-help'});
  const save = control(t('keySave'), null, {type: 'submit', tone: 'primary', disabled: true});
  const status = el('p', {class: 'provider-status', role: 'status'},
    t(preview ? 'keyPreviewOnly' : 'keyChecking'));
  const feedback = el('p', {class: 'provider-feedback', role: 'status'});
  let saving = false;
  let managed = false;
  key.addEventListener('input', () => {
    save.disabled = saving || managed || !key.value.trim();
    feedback.textContent = '';
  });

  const form = el('form', {class: 'model-key-form', onsubmit: async event => {
    event.preventDefault();
    const value = key.value.trim();
    if (preview || saving || managed || !value) return;
    saving = true;
    key.readOnly = true;
    save.disabled = true;
    save.textContent = t('keySaving');
    feedback.textContent = '';
    try {
      await live.saveProviderKey(value);
      key.value = '';
      status.textContent = t('keyConfigured');
      feedback.textContent = t('keySaved');
    } catch (error) {
      managed = error.status === 409;
      if (managed) {
        key.value = '';
        key.disabled = true;
        status.textContent = t('keyManaged');
      } else {
        feedback.textContent = t(error.status === 422 ? 'keyInvalid' : 'keySaveFailed');
      }
    } finally {
      saving = false;
      key.readOnly = false;
      save.disabled = managed || !key.value.trim();
      save.textContent = t('keySave');
    }
  }},
  el('label', {for: key.id, class: 'model-label'}, t('apiKey')),
  key, el('p', {id: 'model-key-help', class: 'muted model-help'}, t('keySetup')),
  status, save, feedback);
  const popup = dialog(t('modelConfig'), el('div', {class: 'model-form'}, form,
    el('label', {for: 'preferred-model', class: 'model-label'}, t('modelChoice')),
    select, el('p', {class: 'muted model-help'}, t('previewModel'))), t);
  popup.addEventListener('close', () => { key.value = ''; });
  if (!preview) {
    void live.provider().then(configured => {
      if (status.textContent === t('keyChecking')) {
        status.textContent = t(configured === null ? 'keyStatusUnavailable' : configured ? 'keyConfigured' : 'keyMissing');
      }
    }).catch(() => {
      if (status.textContent === t('keyChecking')) status.textContent = t('keyStatusUnavailable');
    });
  }
}
