import assert from 'node:assert/strict';
import test from 'node:test';
import {confirmAnswerOnEnter} from '../../app/static/ui/chat/contract-wizard.mjs';

function key(overrides = {}, composing = false) {
  const result = {prevented: false, submissions: 0};
  const form = {requestSubmit() { result.submissions += 1; }};
  confirmAnswerOnEnter({
    key: 'Enter',
    preventDefault() { result.prevented = true; },
    currentTarget: {closest: () => form},
    ...overrides,
  }, composing);
  return result;
}

test('plain Enter confirms a contract item once', () => {
  assert.deepEqual(key(), {prevented: true, submissions: 1});
  assert.deepEqual(key({repeat: true}), {prevented: true, submissions: 0});
});

test('Enter preserves multiline input and Chinese candidate confirmation', () => {
  for (const event of [{shiftKey: true}, {isComposing: true}, {keyCode: 229}]) {
    assert.deepEqual(key(event), {prevented: false, submissions: 0});
  }
  assert.deepEqual(key({}, true), {prevented: false, submissions: 0});
});
