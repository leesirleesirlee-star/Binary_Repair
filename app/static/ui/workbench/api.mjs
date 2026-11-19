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

async function request(path, {method = 'GET', body, headers = {}, retry = true, attachment = false} = {}) {
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
    if (method !== 'GET' && retry) return request(path, {method, body, headers, retry: false, attachment});
    throw Object.assign(new Error('Network unavailable.'), {status: 0, cause: error});
  }
  if (response.status === 401) sessionPromise = null;
  if (response.headers.get(VERSION_HEADER) !== VERSION) {
    throw Object.assign(new Error('The server did not select contract v1.'), {
      status: response.status,
      code: 'unsupported_contract_version',
    });
  }
  return attachment && response.ok ? response : parse(response);
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
const pendingStarts = new Map();

async function start(id, digest) {
  let pending = pendingStarts.get(id);
  if (pending?.digest !== digest) {
    pending = {digest, key: crypto.randomUUID()};
    pendingStarts.set(id, pending);
  }
  const clear = () => {
    if (pendingStarts.get(id) === pending) pendingStarts.delete(id);
  };
  try {
    const reply = await request(jobPath(id) + '/start', {
      method: 'POST',
      headers: {'Idempotency-Key': pending.key},
      body: json({expected_sha256: pending.digest}),
    });
    clear();
    return reply;
  } catch (error) {
    if ([400, 404, 409, 422].includes(error.status) && error.code) clear();
    throw error;
  }
}

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
  downloadArtifact: async (jobId, artifactId) => {
    const path = jobPath(jobId) + '/artifacts/' + encodeURIComponent(artifactId) + '/download';
    const response = await request(path, {
      attachment: true,
      headers: {Accept: 'application/octet-stream'},
    });
    const disposition = response.headers.get('content-disposition') || '';
    const filename = /^attachment;\s*filename="([A-Za-z0-9._-]+)"$/i.exec(disposition)?.[1];
    if (response.status !== 200 || !filename || filename === '.' || filename === '..') {
      throw Object.assign(new Error('The server did not return a valid attachment.'), {
        status: 409, code: 'artifact_unavailable',
      });
    }
    return {file: await response.blob(), filename};
  },
  comparison: (id, runId) => request(jobPath(id) + '/comparison' + query({run_id: runId})),
  provider: async () => {
    const data = await request('/api/assistant/key');
    return typeof data.configured === 'boolean' ? data.configured : null;
  },
  create: async ({defect, file}) => {
    const form = new FormData();
    form.append('binary', file, file.name);
    form.append('defect', defect);
    const data = await request('/api/jobs', {method: 'POST', body: form});
    return data.id;
  },
  save: (id, fields, revision) => request(jobPath(id) + '/contract', {
    method: 'POST',
    body: json({expected_revision: revision, ...fields}),
  }),
  approve: (id, digest, reviewer) => request(jobPath(id) + '/approve', {
    method: 'POST',
    body: json({expected_sha256: digest, reviewer_label: reviewer}),
  }),
  start,
  cancel: (id, runId) => request(jobPath(id) + '/cancel', {
    method: 'POST',
    body: json({run_id: runId}),
  }),
  plan: id => request(jobPath(id) + '/plan', {method: 'POST', body: json({mode: 'deterministic'})}),
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
