// Live conversation engine for the contract-v1 task API.
//
// The transcript stays a rendering of server state: every approval, run state,
// check and verdict comes from an API response. The only locally authored text
// is a short status sentence derived from those values, and it is labelled as a
// local summary, never as a model answer.
import {currentEvaluation, live as defaultApi} from '../workbench/api.mjs';
import {label} from './chat-copy.mjs';
import {canAttachTypedContract, isStaleReview, isTypedReview, missingTypedFields, readTypedContract} from './typed-review.mjs';

const newId = () => crypto.randomUUID();
const ACTIVE = new Set(['queued', 'waiting_for_executor', 'running', 'cancelling']);
const TERMINAL = new Set(['completed', 'failed', 'cancelled']);
const POLL_MS = 2000;
const PENDING_EVAL_MS = 30_000;

const short = value => typeof value === 'string' && value.length > 14
  ? value.slice(0, 10) + '…' + value.slice(-4) : (value || '—');
const ms = value => {
  const parsed = Date.parse(value ?? '');
  return Number.isFinite(parsed) ? parsed : null;
};

export function chatLive({notify = () => {}, api: live = defaultApi} = {}) {
  const chats = new Map();
  let locale = 'en';
  let active = null;
  let timer = null;

  function blank(extra = {}) {
    const chat = {
      id: newId(), jobId: null, title: '', events: [], phase: 'idle', queue: [],
      epoch: 0, revision: 0, attachmentNames: new Map(), reviewScopes: new Map(),
      live: true, loaded: false, seeded: false, contract: null, execution: null,
      attempts: [], data: {}, reviewer: '', requestId: null, planRevision: 0,
      routes: null, routeNotes: {}, checks: 0, started: null, ended: null,
      run: null, reviewed: false, startReady: false, scope: '', planSignature: '',
      evalStamp: '', checksRun: '', summaryStamp: '', statusSay: '', lastError: null,
      ...extra,
    };
    chats.set(chat.id, chat);
    return chat;
  }

  function create() {
    return blank();
  }

  function event(chat, type, payload, step_id = null, run_id = chat.run) {
    const entry = {
      version: 1, type, conversation_id: chat.id, seq: String(chat.events.length + 1),
      run_id: run_id ?? null, step_id, ts: new Date().toISOString(), source: 'live', payload,
    };
    chat.events.push(entry);
    notify(chat, entry);
    return entry;
  }

  function setPhase(chat, phase) {
    if (chat.phase === phase) return;
    chat.phase = phase;
    event(chat, 'run_state', {kind: 'repair', job_id: chat.jobId, status: phase, message_id: null});
  }

  function say(chat, text) {
    if (!text) return;
    const id = newId();
    event(chat, 'assistant_delta', {message_id: id, append: text});
    event(chat, 'assistant_end', {message_id: id, status: 'complete'});
  }

  function fail(chat, error) {
    const code = error?.code || (error?.status === 401 ? 'session_required' : 'capability_unavailable');
    const signature = code + ':' + (error?.status ?? '');
    if (chat.lastError === signature) return;
    chat.lastError = signature;
    event(chat, 'error', {
      code,
      correlation_id: error?.correlationId || 'local',
      retry: {kind: 'none'},
    });
    const detail = error?.status === 401
      ? label(locale, 'sessionExpired')
      : label(locale, 'requestFailed');
    say(chat, detail);
  }

  function clearError(chat) {
    chat.lastError = null;
  }

  function summaryPhase(job) {
    if (job.phase === 'cancelled') return 'cancelled';
    if (job.phase === 'finished') return job.outcome ? 'completed' : 'completed';
    if (job.phase === 'queued') return 'queued';
    if (job.phase === 'screening') return 'running';
    if (job.phase === 'draft' || job.phase === 'awaiting_review') return 'idle';
    return 'unknown';
  }

  function ensureSummaryChat(job) {
    const id = 'job:' + job.id;
    const existing = chats.get(id) ?? [...chats.values()].find(chat => chat.jobId === job.id);
    if (existing) {
      existing.title = job.defect || existing.title;
      if (!existing.loaded) existing.phase = summaryPhase(job);
      return existing;
    }
    const chat = blank({
      id, jobId: job.id, title: job.defect || job.id, phase: summaryPhase(job),
      summary: job, seeded: true,
    });
    chats.delete(chat.id);
    chat.id = id;
    chats.set(id, chat);
    const messageId = 'job:' + job.id + ':request';
    chat.attachmentIds = ['job:' + job.id + ':file'];
    chat.events.push({
      version: 1, type: 'user_message', conversation_id: id, seq: '1', run_id: null,
      step_id: null, ts: new Date().toISOString(), source: 'live',
      payload: {
        message_id: messageId,
        text: job.defect || job.id,
        attachment_ids: chat.attachmentIds,
        delivery: 'accepted',
      },
    });
    return chat;
  }

  function planSteps(chat) {
    const contract = chat.contract || {};
    const execution = chat.execution || {};
    const evaluation = chat.data?.evaluation;
    const hasJob = Boolean(chat.jobId);
    const approved = Boolean(contract.approved);
    const finalized = Boolean(evaluation?.finalized);
    const observedRunning = chat.events.some(event => event.run_id === execution.run_id &&
      event.type === 'run_state' && event.payload.status === 'running');
    const runStatus = ({
      running: 'in_progress', completed: 'completed',
      cancelling: observedRunning ? 'in_progress' : 'pending',
      cancelled: observedRunning ? 'skipped' : 'not_run', failed: 'failed',
    })[execution.status] ?? 'pending';
    const verifyStatus = finalized ? 'completed' : execution.status === 'completed' ? 'in_progress' :
      execution.terminal ? 'not_run' : 'pending';
    const artifacts = (Array.isArray(chat.data?.artifacts) ? chat.data.artifacts : [])
      .filter(item => item.kind === 'run_evidence' || item.kind === 'manual_deliverable');
    return [
      {step_id: 'intake', label_code: 'planIntake',
        status: hasJob ? 'completed' : 'pending'},
      {step_id: 'contract', label_code: 'planContract',
        status: approved ? 'completed' : hasJob ? 'in_progress' : 'pending'},
      {step_id: 'run', label_code: 'planRun',
        status: runStatus},
      {step_id: 'verify', label_code: 'planVerify',
        status: verifyStatus},
      {step_id: 'evidence', label_code: 'planEvidence',
        status: artifacts.length ? 'completed' : finalized ? 'not_run' : 'pending'},
    ];
  }

  function ensurePlan(chat) {
    const steps = planSteps(chat);
    const signature = JSON.stringify(steps);
    if (chat.planSignature === signature) return;
    chat.planSignature = signature;
    chat.planRevision += 1;
    event(chat, 'plan_update', {revision: chat.planRevision, steps});
  }

  function ensureRequest(chat) {
    const contract = chat.contract || {};
    if (!chat.jobId || !contract.revision) return;
    const revision = Number(contract.revision) || 1;
    const requestId = 'contract:' + chat.jobId + ':' + revision;
    if (chat.requestId === requestId) return;
    if (chat.requestId) {
      event(chat, 'user_response', {
        kind: 'decision', request_id: chat.requestId, decision: 'edit',
        binding: {contract_revision: chat.revision, contract_sha256: chat.contract?.sha256 || ''},
      }, 'contract');
    }
    chat.requestId = requestId;
    chat.revision = revision;
    chat.reviewScopes.set(requestId, contract.valid_domain || '');
    const binding = {
      contract_revision: revision,
      contract_sha256: contract.sha256 || '',
      review_binding_digest: contract.review_binding_digest || '',
    };
    event(chat, 'awaiting_user', {
      kind: 'contract_review',
      request_id: requestId,
      label_code: 'approval',
      binding,
      allowed: ['approve', 'deny', 'edit'],
    }, 'contract');
    if (contract.approved) {
      event(chat, 'user_response', {
        kind: 'decision', request_id: requestId, decision: 'approve', binding,
      }, 'contract');
      chat.startReady = true;
    }
  }

  function stepForAttempt(attempt) {
    if (!attempt) return 'pending';
    if (attempt.status === 'finished') return attempt.outcome ? 'completed' : 'not_run';
    if (attempt.status === 'cancelled') return 'not_run';
    if (attempt.status === 'queued') return 'pending';
    return 'in_progress';
  }

  function ensureRoutes(chat) {
    const attempts = Array.isArray(chat.data?.attempts) ? chat.data.attempts : [];
    const evaluation = chat.data?.evaluation;
    const checks = Array.isArray(evaluation?.obligations) ? evaluation.obligations : [];
    const contract = chat.contract || {};
    const routes = {};
    const notes = {};
    for (const route of ['route_a', 'route_b']) {
      const attempt = attempts.filter(row => row.route === route).at(-1);
      const routeChecks = checks.filter(row => row.route === route);
      let verifyStep = 'pending';
      if (routeChecks.length) {
        if (routeChecks.some(row => row.status === 'failed' || row.status === 'timed_out')) verifyStep = 'failed';
        else if (routeChecks.every(row => row.status === 'passed')) verifyStep = 'completed';
        else verifyStep = 'not_run';
      }
      routes[route] = [
        contract.approved ? 'completed' : 'pending',
        stepForAttempt(attempt),
        verifyStep,
      ];
      notes[route] = attempt?.reason || evaluation?.verdict?.reason ||
        (contract.approved ? label(locale, 'routeWaiting') : label(locale, 'scopePending'));
    }
    chat.routes = routes;
    chat.routeNotes = notes;
  }

  function ensureEvaluation(chat) {
    const execution = chat.execution || {};
    const runId = execution.run_id;
    if (!runId) return;
    const evaluation = chat.data?.evaluation;
    if (evaluation?.finalized) {
      const signature = runId + ':final';
      if (chat.checksRun === signature) return;
      chat.checksRun = signature;
      const checks = Array.isArray(evaluation.obligations) ? evaluation.obligations : [];
      for (const check of checks) {
        event(chat, 'verification', {
          kind: 'check',
          route: check.route,
          level: check.level,
          obligation_id: check.route + ':' + check.level,
          status: check.status,
          evidence_ids: [],
          reason_code: check.reason_code ?? null,
        }, 'verify', runId);
      }
      event(chat, 'verification', {
        kind: 'evaluation',
        evaluation_id: runId + ':eval',
        generated_at: new Date().toISOString(),
        verdict: evaluation.verdict?.outcome ? {
          outcome: evaluation.verdict.outcome,
          reason_code: evaluation.verdict.reason ?? null,
        } : null,
      }, 'verify', runId);
      chat.checks = checks.length;
      return;
    }
    const signature = runId + ':' + (evaluation?.finalized ? 'final' : 'pending');
    if (chat.evalStamp === signature) return;
    chat.evalStamp = signature;
    event(chat, 'verification', {
      kind: 'evaluation',
      evaluation_id: runId + ':eval',
      generated_at: new Date().toISOString(),
      verdict: null,
    }, 'verify', runId);
  }

  function ensureRunEnd(chat) {
    const execution = chat.execution || {};
    if (!execution.terminal) return;
    const runId = execution.run_id || 'run';
    const evaluation = chat.data?.evaluation;
    const artifacts = Array.isArray(chat.data?.artifacts) ? chat.data.artifacts : [];
    const signature = [runId, execution.status, evaluation?.finalized, artifacts.length].join(':');
    if (chat.summaryStamp === signature) return;
    chat.summaryStamp = signature;
    const started = chat.started;
    const ended = chat.ended ?? Date.now();
    const terminal = execution.status === 'cancelled' ? 'cancelled'
      : execution.status === 'failed' ? 'failed' : 'completed';
    event(chat, 'run_end', {
      kind: 'repair',
      job_id: chat.jobId,
      execution: terminal,
      duration_ms: started ? Math.max(0, ended - started) : 0,
      steps_taken: chat.checks || null,
      files_changed: artifacts.filter(item => item.kind === 'run_evidence').length,
      ai: {calls: null, tokens: null, cost_usd: null},
      evaluation_id: evaluation?.finalized ? runId + ':eval' : null,
    }, 'verify', runId);
  }

  function sync(chat, snapshot, payload = {}) {
    const contract = snapshot.contract || {};
    const execution = snapshot.execution || {};
    chat.jobId = snapshot.id;
    chat.demo = snapshot.demo;
    chat.title = snapshot.defect || chat.title;
    chat.filename = snapshot.filename || chat.filename;
    const previousDigest = chat.contract?.review_binding_digest;
    chat.typedReview = isTypedReview(contract);
    if (chat.typedReview && previousDigest && previousDigest !== contract.review_binding_digest) {
      chat.reviewRefreshRequired = true;
    }
    if (chat.startAttempt?.digest !== contract.review_binding_digest) chat.startAttempt = null;
    if (execution.run_id && execution.run_id === chat.startAcknowledged) chat.startAcknowledged = null;
    chat.contract = contract;
    chat.technicalSetup = snapshot.technical_setup ?? null;
    chat.execution = execution;
    chat.data = {
      evaluation: currentEvaluation(snapshot, payload.evaluation ?? chat.data?.evaluation),
      attempts: payload.attempts ?? chat.data?.attempts ?? [],
      artifacts: payload.artifacts ?? chat.data?.artifacts ?? [],
      comparison: payload.comparison ?? chat.data?.comparison ?? null,
    };
    chat.run = execution.run_id || null;
    chat.started = ms(execution.started_at) ?? chat.started;
    chat.ended = execution.terminal ? (ms(execution.finished_at) ?? chat.ended ?? Date.now()) : null;
    chat.reviewed = Boolean(contract.approved);
    if (contract.reviewer_label) chat.reviewer = contract.reviewer_label;
    chat.scope = contract.valid_domain || '';
    chat.startReady = chat.reviewed && (!execution.run_id || execution.terminal);
    chat.loaded = true;
    if (chat.attachmentIds?.length) {
      chat.attachmentNames.set(chat.attachmentIds[0], snapshot.filename || chat.attachmentNames.get(chat.attachmentIds[0]));
    }
    if (!chat.seeded) {
      chat.seeded = true;
      const messageId = 'job:' + snapshot.id + ':request';
      const fileId = 'job:' + snapshot.id + ':file';
      chat.attachmentIds = [fileId];
      if (chat.filename) chat.attachmentNames.set(fileId, chat.filename);
      event(chat, 'user_message', {
        message_id: messageId, text: snapshot.defect || snapshot.id,
        attachment_ids: [fileId], delivery: 'accepted',
      }, null, null);
    }
    ensurePlan(chat);
    ensureRequest(chat);
    ensureRoutes(chat);
    ensureEvaluation(chat);
    ensureRunEnd(chat);

    const statusSignature = [execution.status, execution.run_id, contract.approved, contract.revision,
      chat.data.evaluation?.finalized].join(':');
    if (chat.statusSay !== statusSignature) {
      chat.statusSay = statusSignature;
      if (execution.terminal && (chat.data.evaluation?.finalized || execution.status === 'cancelled')) {
        const outcome = chat.data.evaluation?.verdict?.outcome ||
          (execution.status === 'cancelled' ? label(locale, 'cancelled') : label(locale, 'noVerdict'));
        say(chat, label(locale, 'loadedTerminal').replace('{outcome}', outcome));
      } else if (execution.terminal) {
        // Terminal but the evaluation has not committed yet; the summary card
        // and the archive link carry the pending state.
      } else if (execution.run_id) {
        say(chat, label(locale, 'loadedActive').replace('{status}', label(locale, phaseFor(chat))));
      } else if (contract.approved) {
        say(chat, label(locale, 'loadedReady').replace('{revision}', String(contract.revision ?? 1)));
      }
    }

    const pending = execution.terminal && !chat.data.evaluation?.finalized;
    if (pending && !chat.pendingUntil) chat.pendingUntil = Date.now() + PENDING_EVAL_MS;
    if (!pending) chat.pendingUntil = null;

    const phase = terminalPhase(chat) || phaseFor(chat);
    setPhase(chat, phase);
    notify(chat, null);
    return phase;
  }

  function phaseFor(chat) {
    const execution = chat.execution || {};
    const contract = chat.contract || {};
    if (execution.status === 'cancelled') return 'cancelled';
    if (execution.status === 'failed') return 'failed';
    if (execution.status === 'completed') return 'completed';
    if (execution.status === 'cancelling') return 'cancel_requested';
    if (execution.status === 'running') return 'running';
    if (execution.status === 'queued' || execution.status === 'waiting_for_executor') return execution.status;
    if (execution.status === 'unknown') return 'unknown';
    if (contract.approved) return 'ready';
    return 'awaiting_user';
  }

  function terminalPhase(chat) {
    const execution = chat.execution || {};
    return TERMINAL.has(execution.status) ? execution.status : null;
  }

  async function read(chat) {
    const id = chat.jobId;
    if (!id) return null;
    const snapshot = await live.job(id);
    if (snapshot.id !== id) throw new Error('Mismatched job identity');
    const runId = snapshot.execution?.run_id ?? null;
    const [attempts, evaluation, artifacts, comparison] = await Promise.allSettled([
      live.attempts(id),
      runId ? live.evaluation(id, runId) : Promise.resolve(null),
      live.artifacts(id),
      runId ? live.comparison(id, runId) : Promise.resolve(null),
    ]);
    return {
      snapshot,
      attempts: attempts.status === 'fulfilled' ? attempts.value?.attempts ?? [] : [],
      evaluation: evaluation.status === 'fulfilled' ? evaluation.value : null,
      artifacts: artifacts.status === 'fulfilled' ? artifacts.value ?? [] : [],
      comparison: comparison.status === 'fulfilled' ? comparison.value : null,
    };
  }

  async function refresh(chat, {silent = false} = {}) {
    if (!chat.jobId) return;
    const epoch = chat.epoch;
    const sequence = (chat.refreshSequence ?? 0) + 1;
    chat.refreshSequence = sequence;
    try {
      const data = await read(chat);
      if (!data || chat.epoch !== epoch || chat.refreshSequence !== sequence) return;
      clearError(chat);
      sync(chat, data.snapshot, data);
      chat.reviewRefreshPending = false;
      return true;
    } catch (error) {
      if (!silent && chat.refreshSequence === sequence) fail(chat, error);
    }
  }

  function shouldWatch(chat) {
    const execution = chat.execution || {};
    if (chat.startAcknowledged || ACTIVE.has(execution.status)) return true;
    if (execution.terminal && !chat.data?.evaluation?.finalized) {
      return Boolean(chat.pendingUntil) && Date.now() < chat.pendingUntil;
    }
    return false;
  }

  function schedule(chat) {
    clearTimeout(timer);
    timer = null;
    if (active !== chat || !shouldWatch(chat)) return;
    const epoch = chat.epoch;
    timer = setTimeout(async () => {
      if (active !== chat || chat.epoch !== epoch) return;
      await refresh(chat, {silent: true});
      if (active === chat && chat.epoch === epoch) schedule(chat);
    }, POLL_MS);
  }

  function watch(chat) {
    if (active === chat) schedule(chat);
  }

  function deactivate() {
    clearTimeout(timer);
    timer = null;
    active = null;
  }

  async function activate(chat) {
    active = chat;
    if (!chat.jobId) return;
    if (!chat.loaded) await refresh(chat);
    schedule(chat);
  }

  function attach(chat, attachmentId, name) {
    if (attachmentId) chat.attachmentNames.set(attachmentId, name || attachmentId);
  }

  async function send(chat, text, loc, attachmentName = '', attachmentFile = null, typedContract = null) {
    const clean = text.trim();
    if (!clean) return;
    locale = loc;
    if (chat.jobId) {
      followUp(chat, clean);
      return;
    }
    if (!attachmentFile) {
      say(chat, label(locale, 'needFile'));
      return;
    }
    if (!/^[\x00-\x7F]*$/.test(clean)) {
      say(chat, label(locale, 'asciiDefect'));
      return;
    }
    chat.title = clean;
    chat.creationError = null;
    setPhase(chat, 'running');
    const epoch = ++chat.epoch;
    try {
      const jobId = await live.create({
        defect: clean, file: attachmentFile, guided: true, contractText: typedContract?.text,
      });
      if (chat.epoch !== epoch) return;
      chat.jobId = jobId;
      chat.seeded = true;
      const fileId = newId();
      attach(chat, fileId, attachmentName || attachmentFile.name || 'binary');
      const ids = [fileId];
      if (typedContract) {
        const contractId = newId();
        attach(chat, contractId, typedContract.name);
        ids.push(contractId);
      }
      chat.attachmentIds = ids;
      event(chat, 'user_message', {
        message_id: newId(), text: clean, attachment_ids: ids, delivery: 'accepted',
      }, null, null);
      await refresh(chat);
      if (chat.technicalSetup) chat.openContract = true;
      if (!chat.loaded) setPhase(chat, 'awaiting_user');
      say(chat, label(locale, 'jobCreated').replace('{id}', short(jobId)));
      return true;
    } catch (error) {
      if (chat.epoch !== epoch) return;
      chat.creationError = error?.message || label(locale, 'requestFailed');
      setPhase(chat, 'idle');
      fail(chat, error);
      return false;
    }
  }

  async function attachContract(chat, file) {
    if (!file || !canAttachTypedContract(chat) || chat.attachingContract) return;
    chat.attachingContract = true;
    chat.typedAttachmentError = '';
    notify(chat, null);
    try {
      const parsed = await readTypedContract(file);
      await live.attachTypedContract(chat.jobId, parsed.document);
      chat.reviewRefreshRequired = true;
      chat.reviewRefreshPending = true;
      if (!await refresh(chat, {silent: true})) {
        chat.typedAttachmentError = label(locale, 'requestFailed');
      }
    } catch (error) {
      chat.typedAttachmentError = error?.message || label(locale, 'requestFailed');
      if (error.status === 409) await refresh(chat);
    } finally {
      chat.attachingContract = false;
      notify(chat, null);
    }
  }

  async function refreshReview(chat) {
    if (!chat.typedReview && !chat.technicalSetup) return refresh(chat);
    chat.reviewRefreshRequired = true;
    chat.reviewRefreshPending = true;
    notify(chat, null);
    await refresh(chat, {silent: true});
    notify(chat, null);
  }

  async function reviewBinding(chat, digest) {
    if (chat.reviewRefreshPending) return refreshReview(chat);
    if (!digest || digest !== chat.contract?.review_binding_digest) return refreshReview(chat);
    chat.reviewRefreshRequired = false;
    notify(chat, null);
  }

  function reviewReady(chat, renderedDigest) {
    if (renderedDigest !== undefined && renderedDigest !== chat.contract?.review_binding_digest) {
      void refreshReview(chat);
      return false;
    }
    if (chat.reviewRefreshRequired) return false;
    return !chat.typedReview || missingTypedFields(chat.contract).length === 0;
  }

  function followUp(chat, text) {
    event(chat, 'user_message', {
      message_id: newId(), text, attachment_ids: [], delivery: 'accepted',
    }, null, null);
    if (ACTIVE.has(chat.execution?.status)) {
      say(chat, label(locale, 'busyFollowup'));
      return;
    }
    if (!chat.contract?.approved) {
      say(chat, label(locale, 'approvalNeeded'));
      return;
    }
    if (!chat.execution?.run_id) {
      say(chat, label(locale, 'loadedReady').replace('{revision}', String(chat.contract.revision ?? 1)));
      return;
    }
    const evaluation = chat.data?.evaluation;
    if (evaluation?.finalized) {
      const outcome = evaluation.verdict?.outcome || label(locale, 'noVerdict');
      const checks = evaluation.obligations || [];
      const passed = checks.filter(row => row.status === 'passed').length;
      const failed = checks.filter(row => row.status === 'failed' || row.status === 'timed_out').length;
      const notRun = checks.filter(row => row.status === 'not_run' || row.status === 'unknown').length;
      const artifacts = chat.data?.artifacts?.length ?? 0;
      say(chat, label(locale, 'localSummary')
        .replace('{outcome}', outcome)
        .replace('{passed}', String(passed))
        .replace('{failed}', String(failed))
        .replace('{notRun}', String(notRun))
        .replace('{artifacts}', String(artifacts)));
      return;
    }
    say(chat, label(locale, 'evaluationPending'));
  }

  async function decide(chat, requestId, decision, editedFields = null, reviewer = '', renderedDigest) {
    if (!chat.jobId || chat.requestId !== requestId) return;
    const contract = chat.contract || {};
    const binding = {
      contract_revision: contract.revision,
      contract_sha256: contract.sha256 || '',
    };
    if (decision === 'deny') {
      // The v1 contract has no separate deny endpoint. Deny is a local review
      // decision: the backend stays an unapproved draft, and start is blocked.
      event(chat, 'user_response', {
        kind: 'decision', request_id: requestId, decision: 'deny', binding,
      }, 'contract');
      chat.startReady = false;
      setPhase(chat, 'awaiting_user');
      say(chat, label(locale, 'deniedLive'));
      return;
    }
    if (decision === 'approve') {
      if (!reviewReady(chat, renderedDigest)) return;
      const name = String(reviewer || chat.reviewer || '').trim();
      if (!name) {
        say(chat, label(locale, 'reviewerRequired'));
        return;
      }
      if (!contract.review_binding_digest) {
        say(chat, label(locale, 'bindingMissing'));
        return;
      }
      chat.reviewer = name;
      const epoch = chat.epoch;
      try {
        await live.approve(chat.jobId, contract.review_binding_digest, name);
        if (chat.epoch !== epoch) return;
        event(chat, 'user_response', {
          kind: 'decision', request_id: requestId, decision: 'approve', binding,
        }, 'contract');
        chat.startReady = true;
        await refresh(chat);
      } catch (error) {
        fail(chat, error);
        if (isStaleReview(error)) await refreshReview(chat);
      }
      return;
    }
    if (decision === 'edit') {
      const fields = typeof editedFields === 'string'
        ? {valid_domain: editedFields}
        : (editedFields || {});
      if (!fields.valid_domain?.trim()) {
        say(chat, label(locale, 'scopeRequired'));
        return;
      }
      const epoch = chat.epoch;
      try {
        await live.save(chat.jobId, fields, contract.revision);
        if (chat.epoch !== epoch) return;
        event(chat, 'user_response', {
          kind: 'decision', request_id: requestId, decision: 'edit', binding,
        }, 'contract');
        await refresh(chat);
        say(chat, label(locale, 'savedLive'));
      } catch (error) {
        fail(chat, error);
      }
    }
  }

  async function start(chat, renderedDigest) {
    const contract = chat.contract || {};
    if (!chat.jobId || chat.starting || chat.startAcknowledged || !reviewReady(chat, renderedDigest)) return;
    if (!contract.approved) {
      say(chat, label(locale, 'approvalNeeded'));
      return;
    }
    if (ACTIVE.has(chat.execution?.status)) return;
    const digest = contract.review_binding_digest;
    if (!digest) return;
    if (chat.startAttempt?.digest !== digest) chat.startAttempt = {digest, key: newId()};
    const {key} = chat.startAttempt;
    chat.starting = true;
    notify(chat, null);
    const epoch = ++chat.epoch;
    try {
      const result = await live.start(chat.jobId, digest, key);
      if (chat.epoch !== epoch) return;
      if (typeof result?.run_id !== 'string' || !result.run_id) {
        throw new Error('The start response did not identify a run.');
      }
      chat.startAcknowledged = result.run_id;
      chat.startAttempt = null;
      await refresh(chat);
      watch(chat);
    } catch (error) {
      fail(chat, error);
      if (isStaleReview(error)) await refreshReview(chat);
    } finally {
      chat.starting = false;
      notify(chat, null);
    }
  }

  async function stop(chat) {
    const runId = chat.execution?.run_id;
    if (!chat.jobId || !runId || chat.cancelSubmitting || chat.execution.status === 'cancelling') return;
    chat.cancelSubmitting = true;
    notify(chat, null);
    const epoch = ++chat.epoch;
    try {
      await live.cancel(chat.jobId, runId);
      if (chat.epoch !== epoch) return;
      await refresh(chat);
      watch(chat);
    } catch (error) {
      fail(chat, error);
    } finally {
      chat.cancelSubmitting = false;
      notify(chat, null);
    }
  }

  async function upload(chat, file) {
    if (!chat.jobId || !file) return;
    try {
      await live.upload(chat.jobId, file);
      await refresh(chat);
      say(chat, label(locale, 'deliverableAdded'));
    } catch (error) {
      fail(chat, error);
    }
  }

  async function seed(loc) {
    locale = loc;
    try {
      const jobs = await live.list();
      for (const job of jobs) ensureSummaryChat(job);
      if (chats.size) notify(null, null);
    } catch (error) {
      // Keep the composer usable; the error is shown when a turn is attempted.
      const chat = create();
      chat.title = label(locale, 'connection');
      fail(chat, error);
      notify(chat, null);
    }
  }

  function setLocale(loc) {
    locale = loc;
  }

  function reopen(chat) {
    const contract = chat.contract || {};
    if (!chat.jobId || !chat.requestId) return;
    const binding = {
      contract_revision: contract.revision,
      contract_sha256: contract.sha256 || '',
      review_binding_digest: contract.review_binding_digest || '',
    };
    event(chat, 'awaiting_user', {
      kind: 'contract_review',
      request_id: chat.requestId,
      label_code: 'approval',
      binding,
      allowed: ['approve', 'deny', 'edit'],
    }, 'contract');
    setPhase(chat, 'awaiting_user');
  }

  return {
    chats, live: true, create, send, decide, start, stop, upload, seed, activate,
    deactivate, refresh, setLocale, reopen, attachContract, reviewBinding,
  };
}
