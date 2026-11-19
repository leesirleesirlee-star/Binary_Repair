export const bindingFields = [
  ['original_sha256', 'bindingOriginal'],
  ['test_manifest_sha256', 'bindingManifest'],
  ['environment', 'bindingEnvironment'],
  ['patch_policy', 'bindingPatchPolicy'],
  ['verification_scope', 'bindingVerificationScope'],
  ['canonical_contract_sha256', 'bindingCanonical'],
];

export const canonicalLabels = {
  valid_domain: 'valid_domain',
  forbidden: 'forbidden_behavior',
  invalid_outcome: 'invalid_input_response',
  assumptions: 'environment_assumptions',
};

const MAX_CONTRACT_BYTES = 16 * 1024;
const sha256 = value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
const descriptor = value => typeof value === 'object' && value !== null &&
  !Array.isArray(value) && typeof value.label === 'string' && Boolean(value.label.trim());

export async function readTypedContract(file) {
  if (file.size > MAX_CONTRACT_BYTES) {
    throw Object.assign(new Error('The contract exceeds the 16 KiB text limit.'), {code: 'payload_too_large'});
  }
  try {
    const text = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer());
    const document = JSON.parse(text);
    if (!document || typeof document !== 'object' || Array.isArray(document)) {
      throw new TypeError('Expected a JSON object');
    }
    return {name: file.name, text, document};
  } catch {
    throw Object.assign(new Error('Select a UTF-8 contract JSON object.'), {code: 'invalid_input'});
  }
}

export function isTypedReview(contract) {
  const binding = contract?.review_binding;
  return binding?.requires_typed_contract === true || sha256(binding?.typed_contract_sha256);
}

export function missingTypedFields(contract) {
  const binding = contract?.review_binding ?? {};
  return bindingFields.filter(([key]) => {
    const valid = key.endsWith('_sha256') ? sha256 : descriptor;
    return !valid(binding[key]);
  });
}

export function canAttachTypedContract(chat) {
  const binding = chat.contract?.review_binding;
  return chat.typedReview && !binding?.typed_contract_sha256 && !binding?.canonical_contract_sha256 &&
    !chat.execution?.run_id && !chat.contract?.approved && !chat.typedReviewEdited &&
    chat.phase !== 'cancelled';
}

export function noExternalInput(contract, setup) {
  return (setup?.fields?.input_mode ?? contract?.input_mode ??
    contract?.review_binding?.verification_scope?.input_mode) === 'none';
}

export const isStaleReview = error => ['stale_revision', 'stale_approval'].includes(error?.code);
