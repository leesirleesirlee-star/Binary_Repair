import {el} from '../shared/dom.mjs';
import {icon} from '../shared/icons.mjs';
import {stepCard} from './chat-steps.mjs';

export function control(text, onClick, {glyph, compact = false, tone = '', ...attrs} = {}) {
  return el('button', {type: 'button', class: 'control ' + tone + (compact ? ' icon-only' : ''),
    onclick: onClick, 'aria-label': compact ? text : null, title: compact ? text : null, ...attrs},
  glyph ? icon(glyph, 17) : null, compact ? null : text);
}

export function transcript() {
  const items = new Map();
  const commands = new Map();
  const checks = new Map();
  const evaluations = new Map();
  let cursor = 0;
  let active = null;
  const put = (key, event, extra = {}) => items.set(key, {...event, ...extra, key});

  function apply(events) {
    while (cursor < events.length) {
      const event = events[cursor++];
      const {type, payload: p, run_id: run, step_id: step} = event;
      switch (type) {
        case 'user_message': put(p.message_id, event); break;
        case 'message_dispatched': {
          const previous = items.get(p.message_id);
          if (previous) put(p.message_id, previous, {payload: {...previous.payload, delivery: 'accepted'}});
          break;
        }
        case 'assistant_delta': {
          const old = items.get(p.message_id);
          put(p.message_id, old ?? event, {text: (old?.text ?? '') + p.append});
          break;
        }
        case 'assistant_end': {
          const old = items.get(p.message_id);
          if (old) put(p.message_id, old, {finished: p.status});
          break;
        }
        case 'thinking': put(run + ':think:' + step, event); break;
        case 'plan_update': put(run + ':plan', event); break;
        case 'tool_call': put(p.call_id, event); break;
        case 'tool_result': {
          const call = items.get(p.call_id);
          if (call) put(p.call_id, call, {result: p});
          break;
        }
        case 'command': {
          const old = commands.get(p.command_id) ?? {output: '', lines: []};
          const next = p.phase === 'started' ? {...old, ...p, event} :
            p.phase === 'output' ? {...old, output: old.output + p.append, lines: [...old.lines, p]} :
              {...old, ...p};
          commands.set(p.command_id, next);
          if (next.event) put(p.command_id, next.event, {command: next});
          break;
        }
        case 'file_change': put(p.change_id, event); break;
        case 'awaiting_user': put(p.request_id, event); active = p.request_id; break;
        case 'user_response': {
          const old = items.get(p.request_id);
          if (old) put(p.request_id, old, {decision: p.decision ?? 'answered'});
          active = null;
          break;
        }
        case 'error': put(run + ':error:' + event.seq, event); break;
        case 'verification':
          if (p.kind === 'check') checks.set(run, [...(checks.get(run) ?? []), p]);
          else evaluations.set(run, p);
          put(run + ':verification', event, {checks: checks.get(run) ?? [], evaluation: evaluations.get(run)});
          break;
        case 'run_end': {
          if (active && items.get(active)?.run_id === run) {
            put(active, items.get(active), {decision: p.execution});
            active = null;
          }
          for (const [key, item] of items) {
            if (item.run_id === run && (
              (item.type === 'thinking' && item.payload.phase === 'started') ||
              (item.type === 'tool_call' && !item.result) ||
              (item.type === 'command' && item.command.duration_ms == null))) {
              put(key, item, {ended: p.execution});
            }
          }
          const evaluation = evaluations.get(run);
          if (p.kind === 'repair' || p.execution !== 'completed' || p.evaluation_id) {
            put(run + ':summary', event, {evaluation: evaluation?.evaluation_id === p.evaluation_id ? evaluation : null,
              checks: checks.get(run) ?? []});
          }
          break;
        }
      }
    }
    return items.values();
  }
  return {apply};
}

const controlRow = (...children) => el('div', {class: 'controls-row'}, children);
const detail = (summary, ...children) =>
  el('details', {class: 'disclosure'}, el('summary', {}, icon('chevron', 14), summary), el('div', {class: 'detail-body'}, children));
const seconds = ms => (ms / 1000).toFixed(ms < 10_000 ? 1 : 0) + ' s';
const time = (value, locale) => el('time', {datetime: value},
  new Intl.DateTimeFormat(locale, {hour: '2-digit', minute: '2-digit', second: '2-digit'}).format(new Date(value)));
const meta = (...children) => el('span', {class: 'event-meta'}, children);
const chips = (text, mood) => el('span', {class: 'chip', 'data-mood': mood}, text);
const checkRows = (checks, t) => checks.map(check =>
  el('div', {class: 'check-row'}, t(check.route) + ' · ' + check.level, chips(t(check.status), check.status)));

function output(text, t, {diff = false, reveal = false, lines = null, terminal = false} = {}) {
  const copy = control(t('copy'), async event => {
    const button = event.currentTarget;
    try {
      await navigator.clipboard.writeText(text);
      if (button.isConnected) button.textContent = t('copied');
    } catch {
      // Clipboard availability is browser controlled.
    }
  });
  function code(value) {
    if (diff) return el('pre', {tabindex: '0'}, value.split('\n').map(line =>
      el('span', {class: 'diff-line ' + (line.startsWith('+') ? 'addition' : line.startsWith('-') ? 'removal' : '')}, line)));
    if (lines && !reveal) return el('pre', {tabindex: '0'}, lines.map(line =>
      el('span', {class: line.channel === 'stderr' ? 'stderr' : ''}, line.append)));
    return el('pre', {tabindex: '0'}, value);
  }
  return el('div', {class: 'output' + (terminal ? ' terminal-output' : '')},
    el('div', {class: 'output-head'}, t('output'), copy), code(reveal ? text.slice(0, 310) + '…' : text),
    reveal ? detail(t('showMore'), code(text)) : null);
}

function approval(item, ctx) {
  const {t, chat, engine, locale} = ctx;
  const p = item.payload;
  const status = item.decision;
  const scope = chat.reviewScopes.get(p.request_id) ?? '';
  if (status === 'approve') {
    const canStart = chat.startReady && item === ctx.latestApproval && ['completed', 'cancelled'].includes(chat.phase);
    return el('div', {class: 'approval-complete'}, icon('check', 17), t('approved'),
      canStart ? control(t('start'), () => engine.start(chat, locale), {tone: 'primary'}) : null);
  }
  if (status) return el('div', {class: 'activity'},
    t(status === 'deny' ? 'denied' : status === 'edit' ? 'saveReview' : status));

  const form = el('form', {class: 'approval-edit', hidden: true, onsubmit: event => {
    event.preventDefault();
    engine.decide(chat, p.request_id, 'edit', event.currentTarget.elements.scope.value);
  }},
  el('label', {}, t('field'), el('textarea', {name: 'scope', value: scope, required: true, maxlength: '1000'})),
  control(t('saveReview'), null, {type: 'submit', tone: 'primary'}));
  return el('section', {class: 'approval', 'aria-label': t('approval')},
    el('div', {class: 'approval-title'}, el('span', {class: 'approval-icon'}, icon('mark', 17)),
      el('h2', {}, t('approval')), chips(t('revision') + ' ' + p.binding.contract_revision)),
    el('p', {class: 'scope'}, scope),
    detail(t('reviewFields'), el('p', {}, scope), el('code', {}, p.binding.contract_sha256)),
    form, controlRow(control(t('edit'), () => {
      form.hidden = !form.hidden;
      if (!form.hidden) form.elements.scope.focus();
    }),
    control(t('deny'), () => engine.decide(chat, p.request_id, 'deny')),
    control(t('approve'), () => engine.decide(chat, p.request_id, 'approve'), {tone: 'primary'})));
}

function summary(item, t) {
  const {payload: p, evaluation, checks} = item;
  const result = evaluation?.verdict?.outcome;
  return el('section', {class: 'summary-card'},
    el('header', {}, el('h2', {}, t('summary')), meta(seconds(p.duration_ms),
      p.steps_taken == null ? null : ' · ' + p.steps_taken + ' ' + t('steps'))),
    el('strong', {}, result ?? (p.execution === 'cancelled' ? t('cancelled') : t('noVerdict'))),
    p.execution === 'failed' && !result ? el('p', {class: 'muted'}, t('reason')) : null,
    checks.length ? detail(t('checks'), checkRows(checks, t)) : null,
    detail(t('evidence'), t('noEvidence')),
    p.files_changed || p.ai?.tokens != null || p.ai?.cost_usd != null ? detail(t('details'),
      p.files_changed ? el('p', {}, t('changes') + ' · ' + p.files_changed) : null,
      p.ai.tokens != null ? el('p', {}, t('tokens') + ' · ' + p.ai.tokens) : null,
      p.ai.cost_usd != null ? el('p', {}, t('cost') + ' · $' + p.ai.cost_usd) : null) : null);
}

export function renderItem(item, ctx) {
  const {t, locale, chat, engine} = ctx;
  const {payload: p, type, ts} = item;
  const stamp = time(ts, locale);
  const card = (glyph, title, status, milliseconds, ...children) => stepCard({
    glyph, title, status, statusLabel: status ? t(status) : null, time: stamp,
    duration: typeof milliseconds === 'number' ? seconds(milliseconds) : milliseconds, children,
  });
  switch (type) {
    case 'user_message':
      return el('article', {class: 'message user', 'aria-label': t('you')}, el('p', {class: 'message-body'}, p.text),
        p.attachment_ids.map(id => el('span', {class: 'message-file'}, icon('file', 14), chat.attachmentNames.get(id) ?? id)),
        p.delivery === 'queued' ? chips(t('queued')) : null);
    case 'assistant_delta':
      return el('article', {class: 'message assistant', 'aria-label': t('assistant')},
        el('div', {class: 'reply-body'}, item.text, !item.finished ? el('span', {class: 'caret', 'aria-hidden': 'true'}) : null));
    case 'thinking': {
      const thinking = p.phase === 'started' && !item.ended;
      return card('thinking', t(thinking || item.ended ? 'think' : p.summary_code),
        item.ended ?? (thinking ? 'in_progress' : 'completed'),
        thinking ? el('span', {'data-elapsed': ts}, '0 s') : p.duration_ms,
        el('p', {}, t(item.ended ?? (thinking ? 'thoughtDetail' : p.summary_code))));
    }
    case 'plan_update':
      return card('layers', t('plan'), null, p.steps.filter(step => step.status === 'completed').length + ' / ' + p.steps.length,
      el('ol', {class: 'plan'}, p.steps.map(step =>
        el('li', {}, icon(step.status === 'completed' ? 'check' : step.status === 'failed' ? 'x' :
          step.status === 'skipped' ? 'slash' : step.status === 'in_progress' ? 'clock' : 'circle', 16),
        t(step.label_code), chips(t(step.status), step.status)))));
    case 'tool_call':
      return card(p.tool_code === 'read_contract_metadata' ? 'file' : 'tool', t(p.label_code),
        item.result?.status ?? item.ended ?? 'in_progress', item.result?.duration_ms,
        el('p', {class: 'muted'}, p.input_summary), el('code', {}, p.tool_code),
      item.result ? output(item.result.output.text, t) : null);
    case 'file_change':
      return card('edit', t(p.stage) + (p.files.length === 1 ? ' · ' + p.files[0].display_path : ''),
        p.stage, null, p.files.map(file =>
        el('div', {class: 'file-diff'}, el('div', {class: 'diff-heading'}, el('code', {}, file.display_path),
          el('span', {class: 'addition'}, '+' + file.added), el('span', {class: 'removal'}, '−' + file.removed)),
        output(file.diff.text, t, {diff: true}))));
    case 'command': {
      const command = item.command;
      const state = command.phase !== 'finished' ? item.ended ?? 'in_progress' :
        command.exit_code == null ? 'unknown' : command.exit_code === 0 ? 'completed' : 'failed';
      return card('terminal', t(command.label_code), state, command.duration_ms,
      el('p', {class: 'muted'}, t('cwd') + ' ', el('code', {}, command.display_cwd),
        command.exit_code == null ? null : ' · ' + t('exit') + ' ' + command.exit_code),
      command.output ? output(command.output, t, {terminal: true, lines: command.lines,
        reveal: command.output.length > 310}) : null);
    }
    case 'awaiting_user': return approval(item, ctx);
    case 'verification':
      return card('checkCircle', t('verifyActivity'),
        item.checks.length && item.checks.every(check => check.status === 'not_run') ? 'not_run' : null,
        null, checkRows(item.checks, t),
        item.evaluation ? el('p', {}, item.evaluation.verdict?.outcome ?? t('noVerdict')) : null);
    case 'error':
      return card('info', t('error'), 'failed', null,
        el('div', {class: 'error-action'}, t('reason'), p.retry.kind === 'manual' && chat.phase === 'failed' && chat.run === item.run_id ?
          control(t('retry'), () => engine.start(chat, locale, true)) : null),
        detail(t('details'), el('code', {}, p.code + ' · ' + p.correlation_id)));
    case 'run_end': return summary(item, t);
  }
}

export function displayTitle(chat, t) {
  return chat.titleKey ? t(chat.titleKey) : chat.title || t('new');
}
