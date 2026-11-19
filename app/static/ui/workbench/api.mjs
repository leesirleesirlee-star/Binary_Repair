// Client for the contract-v1 browser API. Every request carries the version
// header; writes also carry the session CSRF token. The token is held in
// memory only and never written to storage.
const VERSION_HEADER = 'X-BinaryRepair-Contract-Version';
const VERSION = '1';

let sessionPromise = null;

async function parse(response) {
  const type = response.headers.get('content-type') || '';
  const body = type.includes('json') ? await response.json() : await response.text();
  if (!response.ok) {
    throw Object.assign(new Error(body?.detail || response.statusText), {
      status: response.status,
      code: body?.code,
      correlationId: body?.correlation_id,
      body,
    });
  }
  return body;
}

async function readSession() {
  const response = await fetch('/api/session', {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {Accept: 'application/json', [VERSION_HEADER]: VERSION},
  });
  if (response.headers.get(VERSION_HEADER) !== VERSION) {
    throw Object.assign(new Error('The server did not select contract v1.'), {status: 400});
  }
  const body = await parse(response);
  if (body?.session !== true || typeof body.csrf_token !== 'string') {
    throw Object.assign(new Error('No local session.'), {status: 401, code: 'session_required'});
  }
  return body.csrf_token;
}

function session() {
  sessionPromise ??= readSession().catch(error => {
    sessionPromise = null;
    throw error;
  });
  return sessionPromise;
}

async function request(path, {method = 'GET', body, headers = {}, retry = true} = {}) {
  const token = method === 'GET' ? null : await session();
  const init = {
    method,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
      [VERSION_HEADER]: VERSION,
      ...(token ? {'x-csrf-token': token} : {}),
      ...headers,
    },
  };
  if (body !== undefined) {
    init.body = body;
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if (method !== 'GET' && retry) return request(path, {method, body, headers, retry: false});
    throw Object.assign(new Error('Network unavailable.'), {status: 0, cause: error});
  }
  if (response.status === 401) sessionPromise = null;
  if (response.headers.get(VERSION_HEADER) !== VERSION) {
    throw Object.assign(new Error('The server did not select contract v1.'), {
      status: response.status,
      code: 'unsupported_contract_version',
    });
  }
  return parse(response);
}

const query = (params) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value);
  }
  const text = search.toString();
  return text ? '?' + text : '';
};

const json = value => JSON.stringify(value);
const jobPath = id => '/api/jobs/' + encodeURIComponent(id);
const providerConfigured = async () => {
  const data = await request('/api/assistant/key');
  return typeof data.configured === 'boolean' ? data.configured : null;
};
export const artifactUrl = id => '/api/artifacts/' + encodeURIComponent(id) + '/download';
export const reportUrl = (id, runId) => jobPath(id) + '/export' + query({run_id: runId});

export const live = {
  demo: false,
  writable: true,
  list: async () => {
    const data = await request('/api/jobs?limit=50');
    return Array.isArray(data.jobs) ? data.jobs : [];
  },
  job: id => request(jobPath(id)),
  attempts: id => request(jobPath(id) + '/attempts'),
  evaluation: (id, runId) => request(jobPath(id) + '/evaluation' + query({run_id: runId})),
  artifacts: async id => {
    const data = await request(jobPath(id) + '/artifacts');
    return Array.isArray(data.artifacts) ? data.artifacts : [];
  },
  comparison: (id, runId) => request(jobPath(id) + '/comparison' + query({run_id: runId})),
  provider: async () => {
    const data = await request('/api/assistant/key');
    return typeof data.configured === 'boolean' ? data.configured : null;
  },
  saveProviderKey: key => request('/api/assistant/key', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: json({key}), retry: false,
  }),
  create: async ({defect, file, guided = false, contractText}) => {
    const form = new FormData();
    form.append('binary', file, file.name);
    form.append('defect', defect);
    if (contractText !== undefined) form.append('contract', contractText);
    else if (guided) form.append('guided_contract', 'true');
    const data = await request('/api/jobs', {method: 'POST', body: form, retry: contractText === undefined});
    return data.id;
  },
  attachTypedContract: (id, contract) => request(jobPath(id) + '/typed-contract', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: json({contract}), retry: false,
  }),
  save: (id, fields, revision) => request(jobPath(id) + '/contract', {
    method: 'POST',
    body: json({expected_revision: revision, ...fields}),
  }),
  setup: (id, details, revision) => request(jobPath(id) + '/technical-setup', {
    method: 'POST',
    body: json({expected_revision: revision, ...details}),
  }),
  approve: (id, digest, reviewer) => request(jobPath(id) + '/approve', {
    method: 'POST',
    body: json({expected_sha256: digest, reviewer_label: reviewer}),
  }),
  start: (id, digest, idempotencyKey = crypto.randomUUID()) => request(jobPath(id) + '/start', {
    method: 'POST',
    headers: {'Idempotency-Key': idempotencyKey},
    body: json({expected_sha256: digest}),
  }),
  cancel: (id, runId) => request(jobPath(id) + '/cancel', {
    method: 'POST',
    body: json({run_id: runId}),
  }),
  plan: async id => {
    let configured = null;
    try { configured = await providerConfigured(); } catch { /* key status is best-effort */ }
    if (configured) {
      try {
        return await request(jobPath(id) + '/plan', {method: 'POST', body: json({mode: 'llm'})});
      } catch (error) {
        if (error?.code !== 'plan_transfer_not_authorized') throw error;
        // A non-public-fixture job cannot use the hosted plan; fall back to the
        // deterministic guide, which is always available and is a different
        // answer, never a deterministic plan labelled as the LLM path.
      }
    }
    return request(jobPath(id) + '/plan', {method: 'POST', body: json({mode: 'deterministic'})});
  },
  upload: async (id, file) => {
    const form = new FormData();
    form.append('file', file, file.name);
    return request(jobPath(id) + '/deliverables', {method: 'POST', body: form});
  },
};

const terminal = new Set(['completed', 'failed', 'cancelled']);
export function shouldPoll(job) {
  if (!job || job.phase === 'draft') return false;
  const execution = job.execution;
  if (!execution) return false;
  if (typeof execution.terminal === 'boolean') return !execution.terminal;
  if (execution.status === 'termination_unconfirmed') return true;
  return !terminal.has(execution.status);
}

export function currentEvaluation(job, evaluation) {
  const runId = job.execution?.run_id;
  return runId && evaluation?.available === true &&
    evaluation.job_id === job.id && evaluation.run_id === runId ? evaluation : null;
}
