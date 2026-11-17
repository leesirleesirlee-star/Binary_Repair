// Read adapters use only the documented GUI service routes.
async function get(path) {
  const response = await fetch(path, {
    credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000),
  });
  if (!response.ok) throw Object.assign(new Error('Request failed'), {status: response.status});
  return response.json();
}

const jobPath = id => '/api/jobs/' + encodeURIComponent(id);
export const artifactUrl = id => '/api/artifacts/' + encodeURIComponent(id) + '/download';
export const reportUrl = id => jobPath(id) + '/export';

export const live = {
  demo: false,
  writable: false,
  list: async () => {
    await get('/api/session');
    const data = await get('/api/jobs?limit=50');
    return data.jobs ?? [];
  },
  job: id => get(jobPath(id)),
  attempts: async id => {
    const data = await get(jobPath(id) + '/attempts');
    if (data.available === false || !Array.isArray(data.attempts)) return null;
    return data.attempts.map(attempt => ({...attempt, run_id: attempt.run_id ?? data.run_id}));
  },
  evaluation: id => get(jobPath(id) + '/evaluation'),
  artifacts: async id => {
    const data = await get(jobPath(id) + '/artifacts');
    if (Array.isArray(data.artifacts)) return data.artifacts;
    const groups = Object.values(data).filter(Array.isArray);
    return groups.length ? groups.flat() : null;
  },
  comparison: id => get(jobPath(id) + '/comparison'),
  provider: async () => {
    const data = await get('/api/assistant/key');
    return typeof data.key?.configured === 'boolean' ? data.key.configured : null;
  },
};

const terminal = new Set(['completed', 'done', 'failed', 'cancelled']);
export function shouldPoll(job) {
  if (!job || job.phase === 'draft') return false;
  const execution = job.execution;
  const status = execution?.status;
  // Cancellation and unconfirmed termination need a server decision.
  if (typeof execution?.terminal === 'boolean') return !execution.terminal;
  if (status === 'termination_unconfirmed') return true;
  return !terminal.has(status);
}

export function currentEvaluation(job, evaluation) {
  const runId = job.execution?.run_id;
  return runId && evaluation?.available === true &&
    evaluation.job_id === job.id && evaluation.run_id === runId ? evaluation : null;
}
