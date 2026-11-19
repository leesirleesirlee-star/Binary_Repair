import {currentEvaluation, reportUrl, shouldPoll} from './api.mjs';
import {badge, button, date, download, el, isAscii, notice} from '../shared/dom.mjs';
import {icon} from '../shared/icons.mjs';

export const fieldKeys = ['valid_domain', 'forbidden', 'invalid_outcome', 'assumptions'];
const section = (heading, ...children) => el('section', {class: 'card padded section'}, el('h2', {}, heading), children);
const label = (title, input) => el('label', {class: 'label'}, title, input);
const detail = (title, ...children) => el('details', {}, el('summary', {}, title), children);
const openDetail = (title, ...children) => {
  const node = detail(title, ...children);
  node.open = true;
  return node;
};
const shortHash = value => typeof value === 'string' && value.length > 16 ? value.slice(0, 12) + '…' + value.slice(-4) : value;

const statusOf = job => job.execution?.status || job.phase;

const fieldCanonicalName = {
  valid_domain: 'valid_domain',
  forbidden: 'forbidden_behavior',
  invalid_outcome: 'invalid_input_response',
  assumptions: 'environment_assumptions',
};

function bindingComplete(binding) {
  if (!binding.original_sha256 || !binding.test_manifest_sha256 || !binding.environment ||
      !binding.patch_policy || !binding.verification_scope) return false;
  if (binding.requires_typed_contract && !binding.canonical_contract_sha256) return false;
  return true;
}

function statusReason(t, contract, dirty) {
  if (dirty) return t('saveFirst');
  if (!contract.review_binding_digest || !bindingComplete(contract.review_binding || {})) return t('bindingMissing');
  if (!contract.approved) return t('approvalRequired');
  return '';
}

function bindingValue(value) {
  if (!value) return el('span', {class: 'missing'}, '—');
  if (typeof value === 'string') return el('span', {class: 'mono'}, shortHash(value));
  return el('span', {}, el('strong', {}, value.label), el('span', {class: 'sub'}, value.summary));
}

function contractView(ctx) {
  const {t, job, edits, api, act, busy, plan, error} = ctx;
  const contract = job.contract || {};
  const binding = contract.review_binding || {};
  const textOf = value => Array.isArray(value) ? value.join('\n') : (value ?? '');
  const dirty = fieldKeys.some(key => (edits[key] ?? textOf(contract[key])) !== textOf(contract[key]));
  const active = Boolean(job.execution?.run_id) && job.execution?.terminal !== true;
  const writable = api.writable && api.lifecycle !== false && !active;
  const complete = bindingComplete(binding);
  const cancelled = job.phase === 'cancelled';
  const reason = cancelled ? t('cancelledJob') : statusReason(t, contract, dirty);
  const validate = value => isAscii(value) ? '' : t('ascii');
  const valueOf = key => edits[key] ?? (Array.isArray(contract[key]) ? contract[key].join('\n') : contract[key] ?? '');

  const fields = el('div', {class: 'columns'}, fieldKeys.map(key => label(
    t(key) + ' · ' + fieldCanonicalName[key],
    el('textarea', {
      name: key, rows: key === 'assumptions' ? 3 : 2, value: valueOf(key), readonly: !writable,
      oninput: event => {
        const input = event.currentTarget;
        edits[key] = input.value;
        input.setCustomValidity(validate(input.value));
        ctx.syncContract?.(true);
      },
    }),
  )));

  const save = button(t('save'), null, {type: 'submit', disabled: busy || !writable});
  const planButton = button(t('plan'), () => act(() => api.plan(job.id), {plan: true}), {className: 'subtle', disabled: busy});
  const form = el('form', {onsubmit: event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    const fieldsOut = {assumptions: values.assumptions};
    for (const key of fieldKeys.slice(0, 3)) fieldsOut[key] = values[key];
    act(() => api.save(job.id, fieldsOut, contract.revision), {clearEdits: true, message: 'saved'});
  }}, fields, writable ? el('div', {class: 'actions'}, save, planButton) : null);

  const reviewer = el('input', {name: 'reviewer', required: true, placeholder: t('reviewerPlaceholder'),
    oninput: event => event.currentTarget.setCustomValidity(event.currentTarget.value.trim() ? validate(event.currentTarget.value) : t('required'))});
  const approve = el('form', {class: 'approve-form', onsubmit: event => {
    event.preventDefault();
    const name = new FormData(event.currentTarget).get('reviewer').trim();
    act(() => api.approve(job.id, contract.review_binding_digest, name));
  }}, label(t('reviewer'), reviewer),
  el('div', {class: 'actions'}, button(t('approve'), null, {type: 'submit', disabled: dirty || busy || !complete || !writable})));

  const start = button(t('start'), () => act(() => api.start(job.id, contract.review_binding_digest)),
    {className: 'primary', disabled: Boolean(reason) || busy || !writable || cancelled});
  const reasonText = el('p', {class: 'stat reason', hidden: !reason}, reason);

  const bindingRows = [
    ['originalSha', binding.original_sha256],
    ['testManifest', binding.test_manifest_sha256],
    ['environment', binding.environment],
    ['patchPolicy', binding.patch_policy],
    ['verificationScope', binding.verification_scope],
    ['canonicalContract', binding.canonical_contract_sha256],
  ];

  return section(t('contract'),
    el('div', {class: 'contract-head'},
      el('div', {class: 'inline'},
        badge(contract.approved ? 'approved' : 'unapproved', t),
        el('span', {class: 'stat mono'}, t('revision') + ' ' + (contract.revision ?? '—')),
        !complete ? badge('bindingIncomplete', t) : null),
      contract.sha256 ? el('span', {class: 'stat mono', title: contract.sha256}, t('content') + ' ' + shortHash(contract.sha256)) : null),
    el('div', {class: 'binding'},
      el('p', {class: 'binding-title'}, t('bindingTitle')),
      el('p', {class: 'stat'}, t('bindingHelp')),
      el('dl', {class: 'binding-grid'}, bindingRows.map(([key, value]) =>
        el('div', {}, el('dt', {}, t(key)), el('dd', {}, bindingValue(value))))),
      contract.review_binding_digest ? el('p', {class: 'stat mono', title: contract.review_binding_digest},
        t('bindingDigest') + ' ' + shortHash(contract.review_binding_digest)) : null),
    contract.approved && contract.approved_at ?
      el('p', {class: 'stat'}, t('approvedBy') + ' ' + (contract.reviewer_label || '—') + ' · ' +
        date(contract.approved_at, ctx.language)) : null,
    error && dirty ? detail(t('info'), fieldKeys.map(key =>
      el('p', {class: 'wrap'}, t(key) + ': ' + valueOf(key)))) : null,
    writable ? form : detail(t('contract'), form),
    plan?.plan ? el('div', {class: 'plan'}, el('p', {class: 'stat'}, t('planLabel') + ' · ' + t(plan.plan.mode)),
      el('ol', {}, plan.plan.steps.map(step => el('li', {}, step.title, el('span', {class: 'sub'}, ' ' + step.prompt))))) : null,
    writable ? el('div', {class: 'stack'},
      !contract.approved ? approve : null,
      reasonText,
      el('div', {class: 'actions'}, start)) : null);
}

function runView({t, job, data, api, act, busy}) {
  const execution = job.execution || {};
  const status = statusOf(job);
  const active = shouldPoll(job);
  const runId = execution.run_id;
  const attempts = data.attempts?.attempts ?? [];
  const current = runId ? attempts.filter(attempt => attempt.run_id === runId) : [];
  return section(t('run'),
    el('div', {class: 'run-head'}, badge(status, t),
      runId ? el('span', {class: 'stat mono', title: runId}, t('runId') + ' ' + shortHash(runId)) : null,
      execution.reason_code ? el('span', {class: 'stat'}, t('reason') + ' ' + execution.reason_code) : null),
    data.connectionError ? notice(t('activityUnavailable'), 'error') : null,
    !runId ? notice(t('noRun')) : null,
    el('div', {class: 'columns'}, ['route_a', 'route_b'].map(route => {
      const rows = current.filter(attempt => attempt.route === route);
      return el('div', {class: 'route'}, el('h3', {}, t(route)),
        rows.length ? rows.map(attempt => el('div', {class: 'inline'},
          badge(attempt.status ?? 'unknown', t),
          attempt.outcome ? badge(attempt.outcome, t) : null,
          attempt.candidate_sha256 ? el('span', {class: 'mono stat'}, shortHash(attempt.candidate_sha256)) : null,
          attempt.duration_ms != null ? el('span', {class: 'stat'}, Math.round(attempt.duration_ms / 1000) + ' s') : null)) :
          badge('not_available', t));
    })),
    api.writable && api.lifecycle !== false && active && status !== 'termination_unconfirmed' ?
      el('div', {class: 'actions'}, button(t('cancel'), () => act(() => api.cancel(job.id, runId)),
        {className: 'danger', disabled: busy})) : null);
}

function resultView({t, language, job, data, reload, refresh}) {
  const evaluation = data.evaluation;
  const selected = currentEvaluation(job, evaluation);
  if (!job.execution?.run_id) return section(t('result'), notice(t('noRun')));
  if (!evaluation || evaluation.job_id !== job.id || evaluation.run_id !== job.execution.run_id) {
    return section(t('result'), notice(t('evaluationUnavailable')), el('div', {class: 'actions'},
      button(t('retry'), refresh, {glyph: 'refresh'}) ));
  }
  if (!evaluation.available) {
    return section(t('result'), notice(t('evaluationPending')), el('div', {class: 'actions'},
      button(t('retry'), refresh, {glyph: 'refresh'}) ));
  }
  const outcome = evaluation.finalized ? evaluation.verdict?.outcome : null;
  const tone = {success_within_scope: 'good', inconclusive: 'warning', unsupported: 'neutral',
    rejected: 'bad', error: 'bad', cancelled: 'neutral'}[outcome] ?? 'neutral';
  const rows = Array.isArray(evaluation.obligations) ? evaluation.obligations : [];
  const grouped = ['route_a', 'route_b'].map(route => [route, rows.filter(row => row.route === route)]);
  return section(t('result'),
    el('div', {class: 'verdict' + (outcome ? '' : ' pending'), 'data-tone': tone},
      el('span', {class: 'verdict-label'}, t('verdict')),
      el('strong', {class: outcome ? 'mono' : ''}, outcome || t('noVerdict')),
      evaluation.verdict?.reason ? el('span', {class: 'stat'}, evaluation.verdict.reason) : null),
    !evaluation.finalized ? notice(t('evaluationPending')) : null,
    !outcome && evaluation.finalized ? notice(t('noVerdictReason')) : null,
    outcome ? el('p', {class: 'stat'}, t('serverVerdict')) : null,
    grouped.map(([route, list]) => el('div', {class: 'obligation-group'},
      el('h3', {}, t(route)),
      list.length ? list.map(row => el('div', {class: 'obligation'},
        el('span', {class: 'level'}, row.level),
        el('span', {class: 'stat'}, t(row.level).replace(row.level + ' ', '') + (row.reason_code ? ' · ' + row.reason_code : '')),
        badge(row.status, t))) : el('p', {class: 'stat'}, t('noChecks')) )),
    evaluation.limitations?.length ? openDetail(t('limitations'), evaluation.limitations.map(row =>
      el('p', {class: 'wrap'}, el('code', {class: 'mono'}, row.code), ' — ', row.detail))) : null);
}

function artifactRow(artifact, {t, job, api, act, busy}) {
  const id = artifact.id;
  const downloadable = typeof api.downloadArtifact === 'function' &&
    artifact.available && typeof id === 'string' && !id.startsWith('demo-artifact-');
  return el('div', {class: 'item'}, el('div', {class: 'stack wrap'},
    el('strong', {}, artifact.name || id),
    el('span', {class: 'stat'}, [t(artifact.kind), artifact.role].filter(Boolean).join(' · ') +
      (artifact.size_bytes ? ' · ' + artifact.size_bytes + ' B' : '')),
    artifact.sha256 ? el('code', {class: 'mono'}, artifact.sha256) : null,
    !artifact.available && artifact.unavailable_reason ? el('span', {class: 'stat'}, artifact.unavailable_reason) : null),
    downloadable ? el('button', {class: 'button subtle', type: 'button', disabled: busy,
      onclick: () => act(async () => {
        const {file, filename} = await api.downloadArtifact(job.id, id);
        download(file, filename);
      })},
      icon('download', 16), t('download')) : null);
}

function evidenceView({t, job, data, api, act, busy}) {
  const artifacts = Array.isArray(data.artifacts) ? data.artifacts : [];
  const comparison = data.comparison;
  const runId = job.execution?.run_id;
  return section(t('evidence'),
    el('div', {class: 'subsection'},
      el('h3', {}, t('artifacts') + (artifacts.length ? ' · ' + artifacts.length : '')),
      artifacts.length ? artifacts.map(artifact => artifactRow(artifact, {t, job, api, act, busy})) : notice(t('noArtifacts'))),
    el('div', {class: 'subsection'},
      el('h3', {}, t('comparison')),
      comparison?.status === 'available' ?
        el('div', {class: 'stack'}, badge('available', t),
          el('p', {class: 'wrap'}, comparison.comparison?.summary || comparison.detail || '')) :
        notice(t(comparison?.status === 'not_provided' ? 'noComparison' : 'comparisonInvalid'))),
    el('div', {class: 'actions'}, el('a', {href: reportUrl(job.id, runId), download: true, class: 'button'},
      icon('download', 16), t('export'))),
    el('p', {class: 'stat'}, t('exportNote')),
    api.writable ? detail(t('upload'), el('form', {onsubmit: event => {
      event.preventDefault();
      const input = event.currentTarget.elements.file;
      const file = input.files[0];
      if (!file?.size || !isAscii(file.name)) {
        input.setCustomValidity(t(file?.size ? 'ascii' : 'fileRequired'));
        input.reportValidity();
        return;
      }
      act(() => api.upload(job.id, file), {message: 'added'});
    }}, label(t('upload'), el('input', {type: 'file', name: 'file', required: true,
      onchange: event => event.currentTarget.setCustomValidity(''),
    })), el('div', {class: 'actions'}, button(t('upload'), null, {type: 'submit', disabled: busy})))) : null);
}

export function taskView(ctx) {
  const {t, job, error, message} = ctx;
  const draft = job.phase === 'draft';
  return el('div', {class: 'page'},
    el('div', {class: 'section-head'}, el('div', {class: 'heading'}, el('div', {class: 'inline'},
      el('span', {class: 'stat mono'}, job.id),
      job.demo ? badge('demo', t) : null,
      badge(job.phase, t)),
      el('h1', {}, job.defect || job.id)),
      el('a', {class: 'button subtle', href: '/ui/chat/?job=' + encodeURIComponent(job.id)},
        icon('chat', 16), t('backToChat'))),
    job.demo ? notice(t('demo'), 'demo') : null,
    error ? notice(t(error), 'error') : null,
    message ? el('div', {role: 'status'}, notice(t(message))) : null,
    draft ? contractView(ctx) : runView(ctx),
    draft ? null : resultView(ctx),
    draft ? null : detail(t('contract'), contractView(ctx)),
    draft ? null : evidenceView(ctx));
}
