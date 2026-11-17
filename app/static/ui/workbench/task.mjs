import {artifactUrl, currentEvaluation, reportUrl, shouldPoll} from './api.mjs';
import {badge, button, date, download, el, isAscii, notice} from '../shared/dom.mjs';
import {icon} from '../shared/icons.mjs';

export const fieldKeys = ['valid_domain', 'forbidden', 'invalid_outcome', 'assumptions'];
const statusOf = job => job.execution?.status === 'not_started' ? job.phase : job.execution?.status || job.phase;
const section = (heading, ...children) => el('section', {class: 'card padded section'}, el('h2', {}, heading), children);
const label = (title, input) => el('label', {class: 'label'}, title, input);
const detail = (title, ...children) => el('details', {}, el('summary', {}, title), children);

function contractView(ctx) {
  const {t, job, edits, api, act, busy, plan, error} = ctx;
  const contract = job.contract;
  if (!contract) return section(t('contract'), notice(t('readError')));
  const dirty = fieldKeys.some(key => (edits[key] ?? contract[key] ?? '') !== (contract[key] ?? ''));
  const hasSavedFields = fieldKeys.every(key => Object.hasOwn(contract, key));
  const writable = api.writable && job.phase === 'draft';
  const blocked = !writable || !contract.approved || dirty || busy;
  const validate = value => isAscii(value) ? '' : t('ascii');

  const form = el('form', {onsubmit: event => {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    act(() => api.save(job.id, fields, contract.revision), {clearEdits: true, message: 'saved'});
  }},
  el('div', {class: 'columns'}, fieldKeys.map(key => label(t(key), el('textarea', {
    name: key, rows: 2, value: edits[key] ?? contract[key] ?? '', readonly: !writable,
    oninput: event => {
      const input = event.currentTarget;
      edits[key] = input.value;
      input.setCustomValidity(validate(input.value));
      const changed = fieldKeys.some(field => (edits[field] ?? contract[field] ?? '') !== (contract[field] ?? ''));
      ctx.syncContract?.(changed);
    },
  })))),
  writable ? el('div', {class: 'actions'}, button(t('save'), null, {type: 'submit', disabled: busy}),
    button(t('plan'), () => act(() => api.plan(job.id), {plan: true}), {className: 'subtle', disabled: busy})) : null);

  const approve = el('form', {onsubmit: event => {
    event.preventDefault();
    const reviewer = new FormData(event.currentTarget).get('reviewer').trim();
    act(() => api.approve(job.id, contract.sha256, reviewer));
  }}, label(t('reviewer'), el('input', {name: 'reviewer', required: true,
    oninput: event => event.currentTarget.setCustomValidity(event.currentTarget.value.trim() ? validate(event.currentTarget.value) : t('required')),
  })), el('div', {class: 'actions'}, button(t('approve'), null, {type: 'submit', disabled: dirty || busy})));

  const start = button(t('start'), () => act(() => api.start(job.id)), {className: 'primary', disabled: blocked});
  const reason = el('p', {class: 'stat', hidden: !dirty && contract.approved}, t(dirty ? 'saveFirst' : 'approvalRequired'));
  ctx.syncContract = changed => {
    start.disabled = !writable || !contract.approved || changed || busy;
    approve.querySelector('button').disabled = changed || busy;
    reason.hidden = !changed && contract.approved;
    reason.textContent = t(changed ? 'saveFirst' : 'approvalRequired');
  };

  return section(t('contract'),
    el('div', {class: 'inline'}, badge(contract.approved ? 'approved' : 'unapproved', t),
      Number.isInteger(contract.revision) ? el('span', {class: 'stat'}, t('revision') + ' ' + contract.revision) : null),
    contract.sha256 ? detail(t('hash'), el('p', {class: 'mono'}, contract.sha256)) : null,
    error && dirty && hasSavedFields ? detail(t('info'), fieldKeys.map(key => el('p', {class: 'wrap'}, t(key) + ': ' + contract[key]))) : null,
    writable || hasSavedFields ? form : notice(t('readError')),
    plan ? notice(t('planLabel') + ' · ' + t('planText')) : null,
    job.phase === 'draft' ? el('div', {class: 'stack'},
      writable && !contract.approved ? approve : null,
      reason, writable ? el('div', {class: 'actions'}, start) : null) : null);
}

function runView({t, job, data, api, act, busy}) {
  const status = statusOf(job);
  const active = shouldPoll(job);
  const runId = job.execution?.run_id;
  return section(t('run'), el('div', {class: 'row', 'aria-live': 'polite'}, badge(status, t)),
    data.connectionError ? notice(t('activityUnavailable')) : null,
    el('div', {class: 'columns'}, ['route_a', 'route_b'].map(route => {
      const attempts = (data.attempts ?? []).filter(attempt => attempt.route === route && attempt.run_id === runId && runId);
      return el('div', {class: 'route'}, el('h3', {}, t(route)),
        attempts.length ? attempts.map(attempt => badge(attempt.status ?? 'unknown', t)) : badge('not_available', t));
    })),
    api.writable && active && status !== 'termination_unconfirmed' ?
      el('div', {class: 'actions'}, button(t('cancel'), () => act(() => api.cancel(job.id)), {className: 'danger', disabled: busy})) : null);
}

function resultView({t, language, job, data}) {
  const evaluation = currentEvaluation(job, data.evaluation);
  if (!evaluation) return section(t('result'), notice(t('evaluationUnavailable')));
  const outcome = evaluation.verdict?.outcome;
  const checks = Array.isArray(evaluation.obligations) ? evaluation.obligations :
    (evaluation.levels ?? []).flatMap(level => (level.routes ?? []).map(row => ({...row, level: level.level})));
  const counts = evaluation.counts;
  const score = evaluation.scorecard;
  const denominator = (numerator, total) => Number.isInteger(numerator) && Number.isInteger(total) && total > 0 ?
    numerator + ' / ' + total : t('not_available');
  const metrics = [
    ['obligations', counts?.obligations && denominator(counts.obligations.pass, counts.obligations.total)],
    ['tests', counts?.tests && denominator(counts.tests.passed, counts.tests.total)],
    ['artifacts', counts?.artifacts],
    ['calls', counts?.ai?.calls],
    ['tokens', counts?.ai?.tokens],
    ['scorecard', score?.observability_index],
  ];
  return section(t('result'), el('h3', {class: outcome ? 'mono' : ''}, outcome || t('noVerdict')),
    !outcome && evaluation.not_verdict === true ? notice(t('noVerdictReason')) : null,
    evaluation.verdict?.failing_obligation ? el('p', {class: 'mono'}, evaluation.verdict.failing_obligation) : null,
    evaluation.generated_at ? el('div', {class: 'inline stat'}, t('latest'), date(evaluation.generated_at, language)) : null,
    checks.length ? detail(t('checks'), checks.map(check => el('div', {class: 'item'},
      el('span', {}, [check.route && t(check.route), check.level].filter(Boolean).join(' · ')),
      badge(check.status ?? 'not_available', t)))) : null,
    counts || score ? detail(t('info'), metrics.map(([key, value]) =>
      value == null ? null : el('div', {class: 'item'}, t(key), el('span', {class: 'count'}, value))),
      score?.formula ? el('p', {class: 'stat wrap'}, t('coverageOnly'), ' ', el('code', {class: 'mono'}, score.formula)) : null) : null);
}

function evidenceView({t, job, data, api, act, busy}) {
  const artifacts = data.artifacts;
  const comparison = data.comparison;
  return section(t('evidence'),
    detail(t('artifacts'), artifacts == null ? notice(t('readError')) :
      artifacts.length ? artifacts.map(artifact => {
        const id = artifact.id ?? artifact.artifact_id;
        const filename = artifact.filename ?? id;
        return el('div', {class: 'item'},
          el('div', {class: 'stack wrap'}, el('strong', {}, filename),
            artifact.size_bytes != null ? el('span', {class: 'stat'}, String(artifact.size_bytes) + ' B') : null,
            artifact.sha256 ? el('code', {class: 'mono'}, artifact.sha256) : null),
          typeof id !== 'string' ? null : api.demo ?
            button(t('download'), () => download(api.file(id), filename), {glyph: 'download'}) :
            el('a', {class: 'button', href: artifactUrl(id), download: true}, icon('download', 16), t('download')));
      }) : notice(t('noArtifacts'))),
    detail(t('comparison'), comparison?.status === 'available' ?
      el('div', {class: 'stack'}, badge('available', t),
        comparison.comparison ? el('code', {class: 'mono'}, JSON.stringify(comparison.comparison)) : null) :
      notice(t(comparison?.status === 'not_provided' ? 'noComparison' : 'readError'))),
    el('div', {class: 'actions'}, api.demo ?
      button(t('export'), async () => download(await api.report(job.id), 'synthetic-report.json'), {glyph: 'download'}) :
      el('a', {href: reportUrl(job.id), download: true, class: 'button'}, icon('download', 16), t('export'))),
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
    el('div', {class: 'heading'}, el('div', {class: 'stat mono'}, job.id),
      el('h1', {}, job.defect || job.id)),
    error ? notice(t(error), 'error') : null,
    message ? el('div', {role: 'status'}, notice(t(message))) : null,
    draft ? contractView(ctx) : runView(ctx),
    draft ? null : resultView(ctx),
    draft ? null : evidenceView(ctx),
    draft ? null : detail(t('contract'), contractView(ctx)));
}
