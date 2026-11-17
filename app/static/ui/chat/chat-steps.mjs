import {el} from '../shared/dom.mjs';
import {icon} from '../shared/icons.mjs';

const motion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

// Keep the native summary trigger. Only the disclosure body changes height.
export function disclosure({className, heading, open = false}, ...children) {
  const content = el('div', {class: 'step-content'}, children);
  const panel = el('div', {class: 'step-panel'}, content);
  const summary = el('summary', {}, heading, icon('chevron', 14));
  const details = el('details', {class: className, open}, summary, panel);
  let wanted = open;
  let frame = 0;
  let height = 0;
  let velocity = 0;
  let last = 0;

  function settle() {
    details.open = wanted;
    delete details.dataset.expanded;
    panel.inert = false;
    panel.style.removeProperty('height');
    panel.style.removeProperty('overflow');
    frame = 0;
    velocity = 0;
  }

  function advance(now) {
    if (!details.isConnected || motion()) return settle();
    const dt = Math.min(Math.max((now - last) / 1000, 0), .024);
    last = now;
    const target = wanted ? content.offsetHeight : 0;
    velocity += ((target - height) * 520 - velocity * 37) * dt;
    height += velocity * dt;
    panel.style.height = Math.max(0, height) + 'px';
    if (Math.abs(target - height) < .35 && Math.abs(velocity) < 8) return settle();
    frame = requestAnimationFrame(advance);
  }

  summary.addEventListener('click', event => {
    if (event.defaultPrevented || motion() || typeof requestAnimationFrame !== 'function') return;
    event.preventDefault();
    wanted = frame ? !wanted : !details.open;
    details.dataset.expanded = String(wanted);
    if (!wanted && panel.contains(document.activeElement)) summary.focus({preventScroll: true});
    if (!frame) {
      height = details.open ? panel.getBoundingClientRect().height : 0;
      panel.style.height = height + 'px';
      panel.style.overflow = 'hidden';
      last = performance.now();
      frame = requestAnimationFrame(advance);
    }
    details.open = true;
    panel.inert = !wanted;
  });
  return details;
}

export function stepCard({glyph, title, status, statusLabel, time, duration, children}) {
  const heading = el('span', {class: 'step-heading'},
    el('span', {class: 'step-icon'}, icon(glyph, 18),
      status === 'in_progress' ? el('span', {class: 'step-pulse'}) : null),
    el('span', {class: 'step-name', title}, title),
    statusLabel ? el('span', {class: 'step-status'}, statusLabel) : null,
    duration != null ? el('span', {class: 'step-duration'}, duration) : null);
  const card = disclosure({className: 'step-card', heading},
    time ? el('div', {class: 'step-time'}, time) : null, children);
  if (status) card.dataset.status = status;
  return card;
}

export function refreshStepCard(previous, next) {
  if (!previous.matches('.step-card') || !next.matches('.step-card')) return false;
  const summary = previous.firstElementChild;
  const body = previous.lastElementChild.firstElementChild;
  const nextBody = next.lastElementChild.firstElementChild;
  const focused = body.contains(document.activeElement);
  const openStates = [...body.querySelectorAll('details')].map(detail => detail.open);
  [...nextBody.querySelectorAll('details')].forEach((detail, index) => { detail.open = openStates[index] ?? false; });
  if (next.dataset.status) previous.dataset.status = next.dataset.status;
  else delete previous.dataset.status;
  summary.replaceChildren(...next.firstElementChild.childNodes);
  body.replaceChildren(...nextBody.childNodes);
  if (focused) summary.focus({preventScroll: true});
  return true;
}

export function createStepGroups(log, t) {
  const groups = new Map();
  const slots = new Map();
  const phases = new Map();
  const groupKey = row => row.run_id + ':' + row.step_id;
  function sync(items) {
    slots.clear();
    phases.clear();
    for (const row of items) {
      if (row.type === 'plan_update') phases.set(row.run_id, row.payload.steps);
      if (!row.step_id || !row.run_id) continue;
      const key = groupKey(row);
      slots.set(key, (slots.get(key) ?? 0) + 1);
    }
    for (const [key, group] of groups) {
      const state = phases.get(group.run)?.find(step => step.step_id === group.step)?.status;
      const count = slots.get(key) ?? 0;
      group.count.textContent = t('eventCount', count);
      if (state) group.details.dataset.status = state;
    }
  }

  function target(row, currentRun) {
    if (!row.step_id || !row.run_id) return log;
    const key = groupKey(row);
    if (!groups.has(key)) {
      const count = el('span', {class: 'step-duration'}, t('eventCount', slots.get(key) ?? 1));
      const label = ['review', 'inspect', 'summarize'].includes(row.step_id) ? row.step_id : 'activity';
      const children = el('div', {class: 'phase-steps'});
      const heading = el('span', {class: 'step-heading'}, icon('layers', 16), el('span', {class: 'step-name'}, t(label)), count);
      const details = disclosure({className: 'phase-group', heading, open: row.run_id === currentRun}, children);
      const state = phases.get(row.run_id)?.find(step => step.step_id === row.step_id)?.status;
      if (state) details.dataset.status = state;
      groups.set(key, {children, count, details, run: row.run_id, step: row.step_id});
      log.append(details);
    }
    return groups.get(key).children;
  }

  return {sync, target, clear() { groups.clear(); slots.clear(); log.replaceChildren(); }};
}
