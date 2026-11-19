import assert from 'node:assert/strict';
import test from 'node:test';
import {chatLive} from '../../app/static/ui/chat/chat-live.mjs';
import {live} from '../../app/static/ui/workbench/api.mjs';
import {
  canAttachTypedContract, isTypedReview, missingTypedFields, readTypedContract,
} from '../../app/static/ui/chat/typed-review.mjs';

const hash = character => character.repeat(64);
const file = new File(['{"schema_version":3}'], 'synthetic-contract.json', {type: 'application/json'});
const binary = new File(['Synthetic input'], 'synthetic.elf');
const completeBinding = {
  requires_typed_contract: true,
  original_sha256: hash('a'),
  test_manifest_sha256: hash('b'),
  typed_contract_sha256: hash('c'),
  canonical_contract_sha256: hash('d'),
  environment: {id: 'mock', label: 'Synthetic environment'},
  patch_policy: {id: 'mock', label: 'Synthetic policy'},
  verification_scope: {id: 'mock', label: 'Synthetic scope'},
};

test('attachment parsing preserves multipart JSON text and bounds its UTF-8 size', async () => {
  const exactText = '{ "description": "中文" }\n';
  assert.deepEqual(await readTypedContract(new File([exactText], file.name)), {
    name: file.name, text: exactText, document: {description: '中文'},
  });
  await assert.rejects(readTypedContract(new File(['[]'], file.name)), {code: 'invalid_input'});
  await assert.rejects(readTypedContract(new File([new Uint8Array([0xff])], file.name)), {code: 'invalid_input'});
  await assert.rejects(readTypedContract(new File(['中'.repeat(6000)], file.name)), {code: 'payload_too_large'});
});

test('typed approval readiness includes the canonical digest', () => {
  const missing = {...completeBinding, canonical_contract_sha256: null, typed_contract_sha256: null};
  const contract = {review_binding: missing};
  assert.equal(isTypedReview(contract), true);
  assert.deepEqual(missingTypedFields(contract).map(([key]) => key), ['canonical_contract_sha256']);
  assert.equal(canAttachTypedContract({typedReview: true, contract}), true);
  assert.equal(canAttachTypedContract({typedReview: true, contract, typedReviewEdited: true}), false);
  assert.equal(canAttachTypedContract({typedReview: true, contract, execution: {run_id: 'run-1'}}), false);
  assert.deepEqual(missingTypedFields({review_binding: completeBinding}), []);
});

test('the shared client sends multipart create and JSON attach, and retries a dropped start with the same key', async t => {
  const calls = [];
  const headers = {'content-type': 'application/json', 'X-BinaryRepair-Contract-Version': '1'};
  t.mock.method(globalThis, 'fetch', async (path, request) => {
    calls.push({path, ...request});
    if (path === '/api/session') {
      return new Response(JSON.stringify({session: true, csrf_token: 'synthetic-csrf'}), {headers});
    }
    if (path.endsWith('/start') && calls.filter(call => call.path === path).length === 1) {
      throw new TypeError('Synthetic dropped response');
    }
    const payload = path.endsWith('/start') ? {run_id: 'run-1'} : {id: 'job-1'};
    return new Response(JSON.stringify(payload), {headers});
  });
  const contract = await readTypedContract(file);
  await live.create({defect: 'Synthetic', file: binary, guided: true, contractText: contract.text});
  await live.attachTypedContract('job-1', contract.document);
  assert.deepEqual(await live.start('job-1', hash('f'), 'same-start-key'), {run_id: 'run-1'});
  const [, created, attached, first, retry] = calls;
  assert.deepEqual([...created.body.keys()], ['binary', 'defect', 'contract']);
  assert.equal(created.body.get('contract'), contract.text);
  assert.equal(created.headers['Content-Type'], undefined);
  assert.equal(attached.path, '/api/jobs/job-1/typed-contract');
  assert.deepEqual(JSON.parse(attached.body), {contract: contract.document});
  assert.equal(attached.headers['x-csrf-token'], 'synthetic-csrf');
  assert.equal(first.headers['Idempotency-Key'], 'same-start-key');
  assert.deepEqual(retry.headers, first.headers);
  assert.deepEqual(JSON.parse(retry.body), {expected_sha256: hash('f')});
});

test('the conversation retries the same start intent after a lost response and displays server statuses', async () => {
  const id = 'job-1';
  const contract = {
    revision: 1, sha256: hash('a'), review_binding_digest: hash('f'),
    review_binding: {...completeBinding, typed_contract_sha256: null, canonical_contract_sha256: null},
    approved: false, valid_domain: 'Synthetic',
  };
  let execution = {status: 'not_started', run_id: null, terminal: false};
  let droppedResponse = true;
  let staleApproval = true;
  let failSnapshot = false;
  const starts = [];
  const approvals = [];
  const api = {
    create: async () => id,
    job: async () => {
      if (failSnapshot) throw new Error('Synthetic snapshot failure');
      return {id, demo: true, contract: structuredClone(contract), execution: {...execution}};
    },
    attempts: async () => ({attempts: []}),
    artifacts: async () => [],
    evaluation: async () => null,
    comparison: async () => null,
    attachTypedContract: async (job, document) => {
      assert.equal(job, id);
      assert.deepEqual(document, {schema_version: 3});
      contract.review_binding = completeBinding;
      contract.revision = 2;
      contract.review_binding_digest = hash('e');
    },
    approve: async (job, digest) => {
      approvals.push({job, digest});
      if (staleApproval) {
        staleApproval = false;
        contract.revision = 3;
        contract.review_binding_digest = hash('c');
        throw Object.assign(new Error('Concurrent edit'), {code: 'stale_approval', status: 409});
      }
      contract.approved = true;
    },
    start: async (job, digest, key) => {
      starts.push({job, digest, key});
      execution = {status: 'waiting_for_executor', run_id: 'run-1', terminal: false};
      if (droppedResponse) {
        droppedResponse = false;
        throw Object.assign(new Error('Lost response'), {status: 0});
      }
      return {run_id: 'run-1'};
    },
    cancel: async () => {
      execution.status = 'cancelling';
      failSnapshot = true;
    },
  };
  const engine = chatLive({api});
  const chat = engine.create();
  assert.equal(await engine.send(chat, 'Synthetic', 'en', binary.name, binary), true);
  await engine.decide(chat, chat.requestId, 'approve', null, 'Reviewer');
  assert.deepEqual(approvals, []);
  await engine.attachContract(chat, file);
  assert.equal(chat.contract.revision, 2);
  assert.equal(chat.reviewRefreshRequired, true);
  await engine.reviewBinding(chat, hash('e'));
  await engine.decide(chat, chat.requestId, 'approve', null, 'Reviewer', hash('e'));
  assert.equal(chat.contract.revision, 3);
  assert.equal(chat.reviewRefreshRequired, true);
  await engine.decide(chat, chat.requestId, 'approve', null, 'Reviewer', hash('c'));
  assert.equal(approvals.length, 1);
  await engine.reviewBinding(chat, hash('c'));
  await engine.decide(chat, chat.requestId, 'approve', null, 'Reviewer', hash('c'));
  assert.equal(chat.contract.approved, true);
  await engine.start(chat, hash('c'));
  assert.equal(chat.execution.run_id, null);
  await engine.start(chat, hash('c'));
  assert.deepEqual(starts[0], starts[1]);
  assert.equal(chat.execution.run_id, 'run-1');
  assert.equal(chat.phase, 'waiting_for_executor');
  await engine.stop(chat);
  assert.equal(chat.phase, 'waiting_for_executor');
  failSnapshot = false;
  await engine.refresh(chat);
  assert.equal(chat.execution.status, 'cancelling');
  assert.equal(chat.phase, 'cancel_requested');
  execution = {status: 'cancelled', run_id: 'run-1', terminal: true};
  await engine.refresh(chat);
  const plan = chat.events.findLast(event => event.type === 'plan_update').payload.steps;
  assert.equal(plan.find(step => step.step_id === 'run').status, 'not_run');
  assert.equal(plan.find(step => step.step_id === 'verify').status, 'not_run');
});
