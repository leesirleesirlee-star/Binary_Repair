import {el} from '../shared/dom.mjs';
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

export function openModel({t, model, preference}) {
  const ids = ['default', 'deepseek-flash', 'deepseek-v4-pro'];
  const select = el('select', {id: 'preferred-model', onchange: event => preference('model', event.currentTarget.value)},
    ids.map(id => el('option', {value: id}, id === 'default' ? t('managedModel') : id)));
  select.value = model;
  dialog(t('modelConfig'), el('div', {class: 'model-form'},
    el('div', {}, el('p', {class: 'model-label'}, t('apiKey')),
      el('output', {class: 'provider-status'}, t('managedKey')),
      el('p', {class: 'muted model-help'}, t('keySetup'))),
    el('label', {for: 'preferred-model', class: 'model-label'}, t('modelChoice')),
    select, el('p', {class: 'muted model-help'}, t('previewModel'))), t);
}
