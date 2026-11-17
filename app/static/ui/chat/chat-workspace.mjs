import {el} from '../shared/dom.mjs';
import {icon} from '../shared/icons.mjs';
import {control} from './chat-view.mjs';
import {createStepGroups, disclosure} from './chat-steps.mjs';

export const activityTypes = new Set(['thinking', 'plan_update', 'tool_call', 'file_change',
  'command', 'verification', 'error', 'run_end']);

const fmtTime = seconds => Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
const phases = {
  running: 'executing', awaiting_user: 'awaiting_user', cancel_requested: 'cancel_requested',
  waiting_for_executor: 'waiting_for_executor',
};
const names = ['route_a', 'route_b'];
const branchSteps = {
  route_a: ['scopeReview', 'manifestRead', 'evidenceCheck'],
  route_b: ['scopeReview', 'rebuildEvidence', 'evidenceCheck'],
};

export function createExecution(t, showConversation) {
  const count = el('strong');
  const phase = el('strong');
  const clock = el('strong');
  const metrics = el('div', {class: 'run-metrics'},
    ...[[t('phase'), phase], [t('stepCount'), count], [t('elapsed'), clock]].map(([name, value]) =>
      el('div', {class: 'run-metric'}, el('span', {}, name), value)));

  const scope = el('p');
  const slots = names.map(() => el('div', {class: 'route-slot'}));
  const branches = el('section', {class: 'route-overview', 'aria-label': t('routes')},
    el('div', {class: 'route-scope'}, el('span', {}, t('executionOverview')),
      el('h3', {}, icon('branch', 16), t('reviewedScope')), scope),
    el('div', {class: 'route-connectors', 'aria-hidden': 'true'}), slots);
  const log = el('div', {class: 'trace-log', 'aria-label': t('activity')});
  const groups = createStepGroups(log, t);
  const empty = el('p', {class: 'trace-empty'}, icon('terminal', 19), t('noActivity'));
  const scroll = el('div', {class: 'execution-scroll', tabindex: '0'},
    branches,
    el('section', {class: 'trace'}, el('h3', {}, t('activity')), empty, log));

  const panel = el('section', {class: 'execution-pane glass', id: 'execution-pane', 'aria-labelledby': 'execution-heading'},
    el('header', {class: 'execution-head'}, el('div', {class: 'panel-heading'},
      icon('terminal', 18), el('h2', {id: 'execution-heading'}, t('execution'))),
    control(t('conversation'), showConversation, {glyph: 'chat', tone: 'compact-only'})),
    scroll);

  let stamp;
  function update(chat, items) {
    const plan = items.findLast(item => item.run_id === chat.run && item.type === 'plan_update');
    const state = chat.phase === 'idle' ? 'noRun' : phases[chat.phase] ?? chat.phase;
    const steps = plan?.payload.steps;
    const activeStep = steps?.find(step => step.status === 'in_progress');
    phase.textContent = t(chat.phase === 'running' && activeStep ? activeStep.label_code : state);
    count.textContent = steps ? steps.filter(step => step.status === 'completed').length + ' / ' + steps.length : '—';
    clock.dataset.runStarted = chat.started ?? '';
    clock.dataset.runEnded = chat.ended ?? '';
    clock.textContent = elapsed(clock);
    empty.hidden = items.some(item => activityTypes.has(item.type));
    groups.sync(items.filter(item => activityTypes.has(item.type)));
    const scopeText = chat.reviewed && chat.scope ? chat.scope : t('scopePending');
    if (scope.textContent !== scopeText) scope.textContent = scopeText;

    const routes = chat.routes ?? {};
    const key = JSON.stringify([chat.id, routes, chat.routeNotes]);
    if (key === stamp) return;
    stamp = key;
    names.forEach((route, index) => {
      const states = routes[route] ?? ['pending', 'pending', 'pending'];
      const status = states.includes('in_progress') ? 'in_progress' : states.includes('failed') ? 'failed' :
        states.every(state => state === 'completed') ? 'completed' : states.includes('not_run') ? 'not_run' : 'pending';
      const lastRoute = slots[index].firstElementChild;
      const heading = el('span', {class: 'route-heading'}, el('span', {class: 'route-letter'}, index ? 'B' : 'A'),
        el('span', {class: 'route-name'}, t(route)),
        el('span', {class: 'route-status'}, el('span', {class: 'route-marker', 'aria-hidden': 'true'}), t(status)));
      const node = disclosure({className: 'route-lane', heading, open: lastRoute?.open ?? false},
        el('strong', {}, t(index ? 'routeBMethod' : 'routeAMethod')),
        el('ol', {}, branchSteps[route].map((step, position) => {
          const state = states[position];
          const glyph = state === 'completed' ? 'check' : state === 'failed' ? 'x' :
            state === 'not_run' ? 'slash' : 'circle';
          return el('li', {'data-state': state}, el('span', {class: 'step-dot'}, icon(glyph, 13)),
            el('span', {}, t(step)), el('span', {class: 'visually-hidden'}, t(state)));
        })),
        el('p', {class: 'route-note'}, t(chat.routeNotes?.[route] ?? 'routeNoReason')));
      node.dataset.status = status;
      const restoreFocus = lastRoute?.contains(document.activeElement);
      slots[index].replaceChildren(node);
      if (restoreFocus) node.querySelector('summary').focus({preventScroll: true});
    });
  }
  return {panel, metrics, log, groups, scroll, update, clock};
}

export function elapsed(node) {
  const start = Number(node.dataset.runStarted);
  if (!start) return '—';
  const end = Number(node.dataset.runEnded) || Date.now();
  return fmtTime(Math.max(0, Math.floor((end - start) / 1000)));
}
