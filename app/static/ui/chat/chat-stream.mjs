import {label} from './chat-copy.mjs';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const stepNames = ['review', 'inspect', 'summarize'];
const context = kind => ({kind, job_id: kind === 'repair' ? 'sample-job' : null});
const newId = () => crypto.randomUUID();
const noResult = {evaluation_id: 'sample-evaluation', verdict: null};

export function chatPreview({notify = () => {}, delay = sleep} = {}) {
  const chats = new Map();

  function create() {
    const chat = {id: newId(), events: [], phase: 'idle', title: '', queue: [], epoch: 0, revision: 2,
      attachmentNames: new Map(), reviewScopes: new Map()};
    chats.set(chat.id, chat);
    return chat;
  }

  function event(chat, type, payload, step_id = null, run_id = chat.run) {
    const entry = {
      version: 1, type, conversation_id: chat.id, seq: String(chat.events.length + 1),
      run_id: run_id ?? null, step_id, ts: new Date().toISOString(), source: 'synthetic', payload,
    };
    chat.events.push(entry);
    notify(chat, entry);
    return entry;
  }

  function state(chat, status) {
    chat.phase = status;
    event(chat, 'run_state', {...context(chat.kind), status, message_id: chat.message ?? null});
  }

  function steps(chat, current, statuses) {
    chat.planRevision = (chat.planRevision ?? 0) + 1;
    event(chat, 'plan_update', {revision: chat.planRevision, steps: stepNames.map((name, index) => ({
      step_id: name, label_code: name,
      status: statuses?.[index] ?? (index < current ? 'completed' : index === current ? 'in_progress' : 'pending'),
    }))});
  }

  function begin(chat, kind, message) {
    chat.run = newId();
    chat.epoch += 1;
    chat.kind = kind;
    chat.message = message?.message_id ?? null;
    chat.started = Date.now();
    chat.ended = null;
    chat.routes = null;
    chat.routeNotes = null;
    chat.planRevision = 0;
    chat.checks = 0;
    state(chat, 'running');
    return chat.epoch;
  }

  async function wait(chat, epoch, ms) {
    await delay(ms);
    return chat.epoch === epoch && chat.phase === 'running';
  }

  function finish(chat, execution, evaluation = null) {
    if (chat.reply) {
      event(chat, 'assistant_end', {message_id: chat.reply, status: 'interrupted'});
      chat.reply = null;
    }
    chat.phase = execution;
    chat.ended = Date.now();
    if (execution === 'cancelled' && chat.routes) {
      for (const states of Object.values(chat.routes)) {
        for (const [index, state] of states.entries()) {
          if (state === 'pending' || state === 'in_progress') states[index] = 'not_run';
        }
      }
    }
    chat.epoch += 1;
    event(chat, 'run_end', {...context(chat.kind), execution, duration_ms: chat.ended - chat.started,
      steps_taken: chat.checks, files_changed: 0, ai: {calls: null, tokens: null, cost_usd: null},
      evaluation_id: evaluation});
  }

  function drain(chat) {
    const next = chat.queue.shift();
    if (next) void turn(chat, next, true);
  }

  async function reply(chat, epoch, text) {
    const id = chat.reply = newId();
    for (const piece of text.match(/.{1,6}/gs) ?? []) {
      if (!await wait(chat, epoch, 27)) return false;
      event(chat, 'assistant_delta', {message_id: id, append: piece});
    }
    chat.reply = null;
    event(chat, 'assistant_end', {message_id: id, status: 'complete'});
    return true;
  }

  function requestReview(chat) {
    const binding = {contract_revision: chat.revision, contract_sha256: chat.revision.toString(16).padStart(64, '0')};
    chat.request = {kind: 'contract_review', request_id: newId(), label_code: 'approval',
      binding, allowed: ['approve', 'deny', 'edit']};
    chat.reviewScopes.set(chat.request.request_id, chat.scope);
    chat.phase = 'awaiting_user';
    event(chat, 'awaiting_user', chat.request, 'review');
    state(chat, 'awaiting_user');
  }

  async function turn(chat, message, queued = false) {
    const epoch = begin(chat, 'assistant_turn', message);
    if (queued) event(chat, 'message_dispatched', {message_id: message.message_id});
    event(chat, 'thinking', {phase: 'started'}, 'review');
    if (!await wait(chat, epoch, 950)) return;
    chat.checks = 1;
    event(chat, 'thinking', {phase: 'finished', duration_ms: 950, summary_code: 'thought'}, 'review');
    if (chat.reviewed) {
      if (!await reply(chat, epoch, label(message.locale, 'followupResponse'))) return;
      finish(chat, 'completed');
      drain(chat);
      return;
    }
    steps(chat, 0);
    const call_id = newId();
    event(chat, 'tool_call', {call_id, tool_code: 'read_contract_metadata',
      label_code: 'tool', input_summary: label(message.locale, 'toolInput')}, 'review');
    if (!await wait(chat, epoch, 420)) return;
    event(chat, 'tool_result', {call_id, status: 'completed', duration_ms: 420,
      output: {text: label(message.locale, 'toolOutput'), truncated: false}}, 'review');
    chat.scope = label(message.locale, 'scope');
    if (!await reply(chat, epoch, label(message.locale, 'response'))) return;
    requestReview(chat);
  }

  function send(chat, text, locale, attachment = '') {
    const clean = text.trim();
    if (!clean) return;
    const active = ['running', 'awaiting_user', 'cancel_requested'].includes(chat.phase);
    const attachmentId = attachment ? newId() : null;
    if (attachmentId) chat.attachmentNames.set(attachmentId, attachment);
    const message = {message_id: newId(), text: clean, attachment_ids: attachmentId ? [attachmentId] : [],
      delivery: active ? 'queued' : 'accepted', locale};
    chat.title ||= clean;
    const {locale: _, ...payload} = message;
    event(chat, 'user_message', payload, null, null);
    if (active) chat.queue.push(message);
    else void turn(chat, message);
  }

  function decide(chat, requestId, decision, editedScope) {
    const request = chat.request;
    if (chat.phase !== 'awaiting_user' || request?.request_id !== requestId || !request.allowed.includes(decision)) return;
    if (decision === 'edit' && !editedScope?.trim()) return;
    chat.request = null;
    event(chat, 'user_response', {kind: 'decision', request_id: requestId, decision, binding: request.binding}, 'review');
    if (decision === 'edit') {
      chat.revision += 1;
      chat.scope = editedScope.trim();
      requestReview(chat);
      return;
    }
    chat.reviewed = decision === 'approve';
    chat.startReady = chat.reviewed;
    steps(chat, 0, [chat.reviewed ? 'completed' : 'skipped', 'skipped', 'skipped']);
    finish(chat, 'completed');
    drain(chat);
  }

  async function start(chat, locale, retry = false) {
    if (!(retry ? chat.phase === 'failed' :
      chat.startReady && ['completed', 'cancelled'].includes(chat.phase))) return;
    chat.startReady = false;
    const epoch = begin(chat, 'repair');
    // Preview-only lane states. Live attempts require explicit backend route evidence.
    chat.routes = {
      route_a: ['completed', 'in_progress', 'pending'],
      route_b: ['completed', 'pending', 'pending'],
    };
    chat.routeNotes = {route_a: 'routeAManifestReason', route_b: 'routeBPending'};
    steps(chat, 1);
    event(chat, 'file_change', {change_id: newId(), stage: 'proposed', files: [{
      display_path: 'review.txt', added: 1, removed: 1,
      diff: {text: '--- a/review.txt\n+++ b/review.txt\n@@ -1 +1 @@\n-Review pending\n+Contract reviewed', truncated: false},
    }]}, 'review');

    const command_id = newId();
    event(chat, 'command', {command_id, phase: 'started', label_code: 'command', display_cwd: 'evidence'}, 'inspect');
    if (!await wait(chat, epoch, 720)) return;
    chat.checks = 1;
    event(chat, 'command', {command_id, phase: 'output', channel: 'stdout', append: label(locale, 'commandOutput')}, 'inspect');
    if (!await wait(chat, epoch, 420)) return;
    chat.routes.route_a = ['completed', 'failed', 'not_run'];
    chat.routes.route_b = ['completed', 'not_run', 'not_run'];
    chat.routeNotes = {route_a: 'routeAUnavailable', route_b: 'routeBNotScheduled'};
    event(chat, 'command', {command_id, phase: 'finished', exit_code: 1, duration_ms: 1140}, 'inspect');
    event(chat, 'error', {code: 'no_required_evidence', correlation_id: 'synthetic-request',
      retry: {kind: 'manual'}}, 'inspect');
    for (const route of ['route_a', 'route_b']) {
      event(chat, 'verification', {kind: 'check', route, level: 'V3', obligation_id: 'V3-' + route,
        status: 'not_run', evidence_ids: [], reason_code: 'required_check_not_run'}, 'summarize');
    }
    event(chat, 'verification', {kind: 'evaluation', ...noResult, generated_at: new Date().toISOString()}, 'summarize');
    chat.checks = 3;
    steps(chat, 0, ['completed', 'failed', 'completed']);
    finish(chat, 'failed', noResult.evaluation_id);
    drain(chat);
  }

  async function stop(chat) {
    if (!['running', 'awaiting_user'].includes(chat.phase)) return;
    const epoch = ++chat.epoch;
    chat.request = null;
    state(chat, 'cancel_requested');
    await delay(250);
    if (chat.epoch !== epoch) return;
    finish(chat, 'cancelled');
    drain(chat);
  }

  function seed(locale) {
    const chat = create();
    chat.titleKey = 'archived';
    const message = {message_id: newId(), text: label(locale, 'archivedPrompt'), attachment_ids: [], delivery: 'accepted'};
    event(chat, 'user_message', message, null, null);
    begin(chat, 'assistant_turn', message);
    const message_id = newId();
    event(chat, 'assistant_delta', {message_id, append: label(locale, 'archivedResponse')});
    event(chat, 'assistant_end', {message_id, status: 'complete'});
    event(chat, 'verification', {kind: 'evaluation', ...noResult, generated_at: new Date().toISOString()}, 'summarize');
    finish(chat, 'completed', noResult.evaluation_id);
    return chat;
  }

  return {chats, create, send, decide, start, stop, seed};
}
