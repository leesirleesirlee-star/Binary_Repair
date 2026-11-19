import {el} from '../shared/dom.mjs';
import {icon} from '../shared/icons.mjs';
import {control, displayTitle} from './chat-view.mjs';

export function appMark(className) {
  return el('span', {class: className, 'aria-hidden': 'true'},
    el('img', {class: 'mark-light', src: './assets/mark-light.svg', alt: ''}),
    el('img', {class: 'mark-dark', src: './assets/mark-dark.svg', alt: ''}));
}

export function sidebar({t, list, mobile, conversation, newChat, search, settings, model, archive, close}) {
  const toggle = control(t('pinSidebar'), null, {glyph: 'sidebar', compact: true,
    tone: 'pin-sidebar', 'aria-expanded': 'false', 'aria-pressed': 'false'});
  const navButton = (key, glyph, click, extraClass = '') =>
    el('button', {type: 'button', class: 'control nav-control ' + extraClass,
      'aria-label': t(key), title: t(key), 'aria-current': key === 'conversation' ? 'page' : null, onclick: click}, icon(glyph, 17), el('span', {}, t(key)),
    key === 'search' ? el('kbd', {}, 'Ctrl K') : null);
  const body = el('div', {class: 'source-list'},
    el('header', {class: 'source-head'}, appMark('brand-icon'), el('strong', {}, 'BinaryRepair'),
      mobile ? control(t('close'), close, {glyph: 'x', compact: true}) : null),
    el('div', {class: 'source-actions'},
      navButton('conversation', 'chat', conversation),
      navButton('archive', 'tasks', archive),
      mobile ? null : toggle,
      navButton('new', 'plus', newChat, 'new-chat'),
      navButton('search', 'search', search, 'search-button')),
    el('nav', {'aria-label': t('chats')}, el('div', {class: 'source-label'}, t('recent')), list),
    el('footer', {}, navButton('modelConfig', 'model', model),
      navButton('settings', 'settings', settings)));
  return {body, toggle};
}

export function paintSidebar({chats, selected, lists, navStamp, t, select}) {
  const entries = [...chats.values()].filter(chat => chat.events.length).reverse();
  const stamp = JSON.stringify([selected.id, entries.map(chat => [chat.id, chat.phase])]);
  if (navStamp === stamp) return stamp;
  for (const list of lists) {
    list.replaceChildren(...entries.map(chat => {
      const status = t(chat.phase);
      const waiting = ['awaiting_user', 'waiting_for_executor', 'cancel_requested'].includes(chat.phase);
      return el('button', {type: 'button', class: 'source-item', 'aria-current': chat === selected ? 'page' : null,
        title: displayTitle(chat, t) + ' · ' + status, onclick: () => select(chat)},
      icon('chat', 16), el('span', {class: 'source-title'}, displayTitle(chat, t)),
      el('span', {class: 'source-status', 'data-phase': chat.phase, 'aria-label': status},
        el('span', {class: 'status-dot', 'aria-hidden': 'true'}), waiting ? t('waiting') : status));
    }));
  }
  return stamp;
}
