// These records are display fixtures. No bytes are read, uploaded, or executed.
const contractFields = {
  valid_domain: 'Documented inputs remain supported.',
  forbidden: 'Reject operations outside the stated bounds.',
  invalid_outcome: 'Return the documented error result.',
  assumptions: 'One defect is in scope.',
};

const examples = [
  ['demo-019', 'Validate record length before reading', 'queued', 'waiting_for_executor'],
  ['demo-018', 'Preserve bounds on a record lookup', 'draft', 'not_started'],
  ['demo-017', 'Reject incomplete headers', 'done', 'completed', 'inconclusive'],
  ['demo-016', 'Keep empty input handling consistent', 'verification', 'running'],
  ['demo-015', 'Preserve documented parser behavior', 'done', 'completed', 'success_within_scope'],
  ['demo-014', 'Check metadata before use', 'failed', 'termination_unconfirmed', 'error'],
  ['demo-013', 'Reject invalid record index', 'cancelled', 'cancelled', 'cancelled'],
];

const demoHash = revision => revision.toString(16).padStart(64, '0');
const makeContract = (values, revision, approved = false) => ({
  ...values, revision, sha256: demoHash(revision), approved,
});

const jobs = new Map(examples.map(([id, defect, phase, status, outcome], index) => [id, {
  id, defect, phase, demo: true, outcome,
  created_at: new Date(Date.UTC(2026, 10, 17, 8 - index, 15)).toISOString(),
  execution: { status, run_id: phase === 'draft' ? null : 'run-' + id },
  contract: makeContract(contractFields, 1, phase !== 'draft'),
  artifacts: [],
}]));

const copy = value => structuredClone(value);
const items = () => [...jobs.values()].reverse().sort((a, b) => b.created_at.localeCompare(a.created_at));
const find = id => {
  const job = jobs.get(id);
  if (!job) throw Object.assign(new Error('Missing fixture'), {status: 404});
  return job;
};

const fileContents = new Map();
const sampleChecks = {
  'demo-017': ['passed', 'failed', 'unknown', 'not_run', 'not_run', 'not_run'],
  'demo-015': ['passed', 'passed', 'passed', 'passed', 'passed', 'passed'],
};
function levels(job) {
  return ['route_a', 'route_b'].flatMap(route => Array.from({length: 6}, (_, level) => ({
    level: 'V' + level,
    route,
    status: sampleChecks[job.id]?.[level] ?? 'not_run',
  })));
}

export const preview = {
  demo: true,
  writable: true,
  list: async () => copy(items()),
  job: async id => copy(find(id)),
  provider: async () => null,
  attempts: async id => {
    const {execution} = find(id);
    return execution.run_id ? ['route_a', 'route_b'].map(route => ({
      route, run_id: execution.run_id,
      status: execution.status === 'waiting_for_executor' ? 'not_started' : execution.status,
    })) : [];
  },
  evaluation: async id => {
    const job = find(id);
    return {
      job_id: job.id,
      run_id: job.execution.run_id,
      available: true,
      generated_at: job.created_at,
      verdict: {outcome: job.outcome ?? null},
      not_verdict: !job.outcome,
      obligations: levels(job),
    };
  },
  artifacts: async id => copy(find(id).artifacts),
  comparison: async () => ({status: 'not_provided'}),
  create: async ({defect}) => {
    const id = 'demo-' + crypto.randomUUID();
    jobs.set(id, {
      id, defect, phase: 'draft', demo: true, created_at: new Date().toISOString(),
      execution: {status: 'not_started', run_id: null},
      contract: makeContract({}, 1), artifacts: [],
    });
    return id;
  },
  plan: async () => ({mode: 'deterministic'}),
  save: async (id, fields, revision) => {
    const job = find(id);
    if (job.contract.revision !== revision) {
      throw Object.assign(new Error('Stale revision'), {status: 409, code: 'stale_revision'});
    }
    job.contract = makeContract(fields, revision + 1);
  },
  approve: async (id, hash) => {
    const job = find(id);
    if (job.contract.sha256 !== hash) {
      throw Object.assign(new Error('Stale approval'), {status: 409, code: 'stale_approval'});
    }
    job.contract.approved = true;
  },
  start: async id => {
    const job = find(id);
    if (job.phase !== 'draft' || !job.contract.approved) return;
    job.phase = 'queued';
    job.execution = {status: 'waiting_for_executor', run_id: 'run-' + id};
  },
  cancel: async id => {
    const job = find(id);
    job.phase = 'cancelled';
    job.execution.status = 'cancelled';
  },
  upload: async (id, file) => {
    const artifact = {id: crypto.randomUUID(), filename: file.name, size_bytes: file.size};
    find(id).artifacts.push(artifact);
    fileContents.set(artifact.id, file);
  },
  file: id => fileContents.get(id),
  report: async id => new Blob([JSON.stringify({
    demo: true, data_source: 'synthetic-preview', not_verdict: true,
    job_id: id, evaluation: await preview.evaluation(id),
  }, null, 2)], {type: 'application/json'}),
};
