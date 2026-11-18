import {icon} from './icons.mjs';

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'class') node.className = value;
    else if (key === 'value') node.value = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  node.append(...children.flat(Infinity).filter(value => value !== null && value !== undefined && value !== false));
  return node;
}

export const button = (text, onClick, options = {}) => {
  const {glyph, className = '', ...attributes} = options;
  return el('button', {type: 'button', class: 'button ' + className, onclick: onClick, ...attributes},
    glyph ? icon(glyph, 16) : null, text);
};

export function notice(message, className = '') {
  return el('p', {class: 'notice ' + className, role: className === 'error' ? 'alert' : null},
    icon('info', 16), el('span', {class: 'wrap'}, message));
}

const statusStyle = {
  approved: ['good', 'check'], passed: ['good', 'check'],
  success_within_scope: ['good', 'check'],
  waiting_for_executor: ['warning', 'clock'], queued: ['warning', 'clock'],
  inconclusive: ['warning', 'info'], unsupported: ['neutral', 'info'],
  failed: ['bad', 'x'], rejected: ['bad', 'x'], error: ['bad', 'x'],
  not_run: ['neutral', 'slash'], unknown: ['unknown', 'info'],
  cancelled: ['neutral', 'slash'],
  termination_unconfirmed: ['warning', 'info'],
};

export function badge(status, t) {
  const [tone, glyph] = statusStyle[status] ?? ['neutral', null];
  return el('span', {class: 'badge', 'data-tone': tone},
    glyph ? icon(glyph, 13) : null, t(status));
}

export function date(value, language) {
  const time = new Date(value);
  return value && Number.isFinite(time.valueOf()) ?
    el('time', {datetime: time.toISOString(), class: 'muted'}, new Intl.DateTimeFormat(language, {dateStyle: 'medium'}).format(time)) :
    el('span');
}

export function download(file, filename) {
  if (!file) return;
  const url = URL.createObjectURL(file);
  const link = el('a', {href: url, download: filename, hidden: true});
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const taskHref = id => '#task/' + encodeURIComponent(id);
export const isAscii = value => /^[\x00-\x7F]*$/.test(value);
