import {el} from '../shared/dom.mjs';
import {live} from '../workbench/api.mjs';
import {bindingFields, canAttachTypedContract, canonicalLabels, isStaleReview, missingTypedFields, noExternalInput} from './typed-review.mjs';

const questions = [
  ['valid_domain', ['Valid input', '有效输入'], ['What input should the program normally accept?', '程序通常应该接受什么输入？']],
  ['forbidden', ['Prohibited behavior', '禁止行为'], ['What actions must not take place?', '必须禁止哪些行为？']],
  ['invalid_outcome', ['Invalid-input response', '异常处理'], ['How should the program respond to invalid input?', '程序应如何响应无效输入？']],
  ['assumptions', ['Environmental assumptions', '环境假设'], ['Under what conditions does this contract operate?', '此契约在什么条件下适用？']],
];

const setupCards = [
  ['Repair target', '修复目标'],
  ['Execution settings', '运行设置'],
  ['Verification inputs', '验证输入'],
  ['Confirm technical setup', '确认技术配置'],
];

const targets = [
  ['target_entry', ['Target symbol or address', '目标符号或地址'], 'text'],
  ['target_signature', ['Target signature', '目标签名'], 'text'],
  ['authorized_edits', ['Authorized edits (one per line)', '获准修改（每行一项）'], 'list'],
  ['allowed_calls', ['Allowed calls (one per line, optional)', '获准调用（每行一项，可选）'], 'list'],
];
const execution = [
  ['environment', ['Execution environment', '运行环境'], 'text'],
  ['input_mode', ['Input mode', '输入方式'], 'mode'],
  ['valid_behavior', ['Expected valid behavior', '预期正常行为'], 'area'],
  ['dest_capacity_bytes', ['Destination capacity in bytes', '目标容量（字节）'], 'number'],
  ['capacity_includes_terminator', ['Capacity includes a terminator', '容量包含终止符'], 'boolean'],
];
const rejection = [
  ['max_valid_input_bytes', ['Maximum valid input in bytes', '有效输入上限（字节）'], 'number'],
  ['invalid_exit_code', ['Invalid-input exit code', '无效输入退出码'], 'number'],
  ['invalid_stdout', ['Expected stdout (empty allowed)', '预期标准输出（可为空）'], 'area'],
  ['invalid_stderr', ['Expected stderr (empty allowed)', '预期标准错误（可为空）'], 'area'],
];
const setupDefaults = {
  input_mode: 'argv', capacity_includes_terminator: true, authorized_edits: [], allowed_calls: [],
  invalid_stdout: '', invalid_stderr: '',
};
const blockerLabels = {
  repair_target: ['Repair target', '修复目标'],
  execution_settings: ['Execution settings', '运行设置'],
  test_manifests: ['Test manifests', '测试清单'],
  ...Object.fromEntries(questions.map(([key, name]) => [key, name])),
};

const action = (name, onclick, attrs = {}) => el('button', {type: 'button', class: 'control', onclick, ...attrs}, name);
const primary = {class: 'control primary'};
const namesFor = mode => mode === 'none' ? ['valid.json'] : ['valid.json', 'invalid.json'];

function savedAnswers(contract) {
  return Object.fromEntries(questions.map(([key]) => [key,
    Array.isArray(contract[key]) ? contract[key].join('\n') : contract[key] ?? '']));
}

function changedAnswers(draft, effectiveValues) {
  return Object.fromEntries(Object.entries(effectiveValues)
    .filter(([key, value]) => value !== draft.base[key]));
}

function conflictLabel(locale) {
  return locale === 'en' ? 'The contract changed in another session.' : '契约已在另一会话中更改。';
}

export function confirmAnswerOnEnter(event, composing = false) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || composing || event.keyCode === 229) return;
  event.preventDefault();
  if (!event.repeat) event.currentTarget.closest('form').requestSubmit();
}

export function createContractWizard({locale, refresh, repaint}) {
  const answers = new Map();
  const setups = new Map();
  const lang = () => locale() === 'en' ? 0 : 1;
  const copy = (en, zh) => lang() ? zh : en;
  const dialog = el('dialog', {class: 'contract-wizard', 'aria-labelledby': 'wizard-title'});
  let open = null;
  dialog.addEventListener('close', () => {
    if (dialog.open || !open) return;
    const anchor = open.anchor;
    open = null;
    repaint();
    if (anchor?.isConnected) anchor.focus({preventScroll: true});
    else document.querySelector('.guided-contract button')?.focus({preventScroll: true});
  });

  function show(chat, kind, step, anchor) {
    const table = kind === 'setup' ? setups : answers;
    const contract = chat.contract;
    let draft = table.get(chat.jobId);
    if (!draft) {
      draft = kind === 'setup' ? {
        step: 0, revision: contract.revision,
        fields: {...setupDefaults, ...chat.technicalSetup.fields},
        files: {}, existing: chat.technicalSetup.manifests,
        existingMode: chat.technicalSetup.fields.input_mode, reviewer: chat.technicalSetup.confirmed_by ?? '',
      } : {
        step: 0, revision: contract.revision, digest: contract.review_binding_digest,
        base: savedAnswers(contract), changes: {},
        noInput: noExternalInput(contract, chat.technicalSetup),
      };
      table.set(chat.jobId, draft);
    }
    if (step !== undefined) {
      draft.step = step;
      draft.single = true;
    }
    open = {chat, kind, draft, anchor};
    render();
    if (!dialog.open) dialog.showModal();
    focusStep();
  }

  function focusStep() {
    dialog.querySelector('[data-step-focus]')?.focus();
  }

  function render() {
    const {chat, draft, kind} = open;
    const setup = kind === 'setup';
    const last = setup ? setupCards.length - 1 : questions.length - 1;
    const title = setup ? copy('Technical setup', '技术配置') : copy('Fill in the repair contract', '填写修复契约');
    const heading = setup ? setupCards[draft.step][lang()] : questions[draft.step][1][lang()];
    const footer = el('footer', {class: 'wizard-actions'},
      action(copy('Close', '关闭'), () => dialog.close()),
      el('span', {class: 'wizard-spacer'}),
      !draft.single && draft.step > 0 ? action(copy('Back', '上一步'), () => go(-1), {disabled: draft.busy}) : null,
      action(draft.step === last || draft.single
        ? (setup ? copy('Confirm setup', '确认配置') : copy('Complete', '完成填写'))
        : copy('Next', '下一步'), null, {
        ...primary, type: 'submit', disabled: draft.busy || Boolean(draft.latest),
      }));
    const liveNotice = el('p', {class: 'wizard-error', role: 'alert'}, draft.error ?? '');
    dialog.replaceChildren(el('form', {
      onsubmit: event => { event.preventDefault(); void next(last); },
    }, el('header', {class: 'wizard-header'},
      el('span', {class: 'wizard-count'}, copy(
        'Item ' + (draft.step + 1) + ' of ' + (last + 1),
        '第 ' + (draft.step + 1) + ' 项，共 ' + (last + 1) + ' 项')),
      el('h2', {id: 'wizard-title'}, title)),
    el('div', {class: 'wizard-body'},
      el('h3', {
        tabindex: '-1', 'data-step-focus': setup || (draft.noInput && draft.step === 2),
        onkeydown: event => {
          if (!setup && draft.noInput) confirmAnswerOnEnter(event);
        },
      }, heading),
      setup ? setupStep(chat, draft) : answerStep(draft),
      draft.latest ? conflictView(draft, setup) : null,
      liveNotice),
    footer));
  }

  function go(delta) {
    open.draft.step += delta;
    open.draft.error = '';
    render();
    focusStep();
  }

  function answerStep(draft) {
    const [key, , prompt] = questions[draft.step];
    if (draft.noInput && key === 'invalid_outcome') {
      return el('p', {}, copy('Not applicable. This task has no external input.', '不适用。此任务没有外部输入。'));
    }
    let composing = false;
    return el('label', {class: 'wizard-field'}, prompt[lang()], el('textarea', {
      rows: '4', 'data-step-focus': true,
      placeholder: draft.base[key],
      value: draft.changes[key] ?? '',
      disabled: draft.busy,
      oncompositionstart: () => { composing = true; },
      oncompositionend: () => { composing = false; },
      oninput: event => { draft.changes[key] = event.currentTarget.value; },
      onkeydown: event => confirmAnswerOnEnter(event, composing),
    }));
  }

  function setupField(draft, [key, name, type]) {
    const value = draft.fields[key];
    const update = event => {
      const input = event.currentTarget;
      draft.fields[key] = type === 'list' ? input.value.split('\n').map(v => v.trim()).filter(Boolean) :
        type === 'number' ? (input.value === '' ? undefined : input.valueAsNumber) :
        type === 'boolean' ? input.checked : input.value;
    };
    let field;
    if (type === 'mode') {
      field = el('select', {onchange: event => {
        update(event);
        render();
        dialog.querySelector('select')?.focus();
      }}, [['argv', copy('Command-line arguments', '命令行参数')],
        ['stdin', copy('Standard input', '标准输入')], ['none', copy('No external input', '无外部输入')]]
        .map(([mode, title]) => el('option', {value: mode, selected: mode === value}, title)));
    } else if (type === 'boolean') {
      field = el('input', {type: 'checkbox', checked: value, onchange: update});
    } else {
      const attrs = {
        required: !['allowed_calls', 'invalid_stdout', 'invalid_stderr'].includes(key),
        value: Array.isArray(value) ? value.join('\n') : value ?? '',
        oninput: update,
      };
      field = type === 'area' || type === 'list' ? el('textarea', {...attrs, rows: '2'}) :
        el('input', {...attrs, type, min: type === 'number' ? (key === 'dest_capacity_bytes' ? '1' : '0') : null,
          max: key === 'invalid_exit_code' ? '255' : null, step: type === 'number' ? '1' : null});
    }
    field.disabled = Boolean(draft.busy);
    return el('label', {class: type === 'boolean' ? 'wizard-check' : 'wizard-field'}, name[lang()], field);
  }

  function setupStep(chat, draft) {
    if (draft.step === 0) return targets.map(field => setupField(draft, field));
    if (draft.step === 1) return [
      ...execution.map(field => setupField(draft, field)),
      ...(draft.fields.input_mode === 'none' ? [] : rejection.map(field => setupField(draft, field))),
    ];
    if (draft.step === 2) {
      const original = chat.contract.review_binding?.original_sha256;
      return [
        el('p', {}, copy('Attach manifests bound to this original SHA-256.', '请附上与此原始 SHA-256 绑定的测试清单。')),
        original ? el('code', {}, original) : null,
        ...namesFor(draft.fields.input_mode).map(name => el('label', {class: 'wizard-field'},
          name, el('span', {class: 'wizard-muted'}, draft.files[name]?.name ??
            (canReuse(draft, name) ? copy('Saved manifest', '已保存的清单') : '')),
          el('input', {type: 'file', accept: '.json,application/json',
            required: !draft.files[name] && !canReuse(draft, name),
            onchange: event => {
              const file = event.currentTarget.files[0];
              if (file) draft.files[name] = file;
            }}))),
        el('p', {}, copy('Required checks: V0–V5 for both routes.', '必需检查：两条路线均执行 V0–V5。')),
      ];
    }
    const fields = [...targets, ...execution, ...(draft.fields.input_mode === 'none' ? [] : rejection)];
    return [
      el('dl', {class: 'wizard-recap'}, fields.map(([key, name]) => {
        const value = draft.fields[key];
        const text = typeof value === 'boolean' ? (value ? copy('Yes', '是') : copy('No', '否')) :
          (Array.isArray(value) ? value.join('\n') : String(value ?? ''));
        return el('div', {}, el('dt', {}, name[lang()]), el('dd', {}, text || '—'));
      }), el('div', {}, el('dt', {}, copy('Original SHA-256', '原始文件 SHA-256')),
        el('dd', {}, chat.contract.review_binding?.original_sha256)),
      el('div', {}, el('dt', {}, copy('Test manifests', '测试清单')),
        el('dd', {}, namesFor(draft.fields.input_mode).join(', ')))),
      el('label', {class: 'wizard-field'}, copy('Reviewer name', '审阅人姓名'), el('input', {
        required: true, maxlength: '128', value: draft.reviewer, disabled: draft.busy,
        oninput: event => { draft.reviewer = event.currentTarget.value; },
      })),
    ];
  }

  function canReuse(draft, name) {
    return draft.existingMode === draft.fields.input_mode && draft.existing.includes(name);
  }

  function effective(draft) {
    return Object.fromEntries(questions.map(([key]) => [key, key === 'invalid_outcome' && draft.noInput
      ? '' : draft.changes[key]?.trim() || draft.base[key]]));
  }

  function conflictView(draft, setup) {
    const latest = draft.latest;
    const saved = savedAnswers(latest.contract);
    const typed = setup ? null : effective(draft);
    return el('section', {class: 'wizard-conflict'}, el('p', {}, conflictLabel(locale())),
      el('p', {}, copy('Latest revision ', '最新版本 ') + latest.contract.revision),
      setup ? el('dl', {class: 'wizard-recap'}, Object.entries(latest.technical_setup.fields)
        .map(([key, value]) => el('div', {}, el('dt', {}, key), el('dd', {}, String(value))))) :
        el('dl', {class: 'wizard-recap'}, questions.map(([key, name]) => el('div', {},
          el('dt', {}, name[lang()]),
          el('dd', {}, copy('Saved: ', '已保存：') + saved[key]),
          el('dd', {}, copy('Your draft: ', '你的草稿：') + typed[key])))),
      action(copy('Review latest and keep my draft', '核对最新版本并保留草稿'), () => {
        draft.revision = latest.contract.revision;
        if (setup) {
          draft.existing = latest.technical_setup.manifests;
          draft.existingMode = latest.technical_setup.fields.input_mode;
        } else {
          draft.changes = changedAnswers(draft, typed);
          draft.digest = latest.contract.review_binding_digest;
          draft.base = saved;
          draft.noInput = noExternalInput(latest.contract, latest.technical_setup);
        }
        draft.latest = null;
        draft.error = '';
        render();
        focusStep();
      }),
      action(copy('Discard draft', '放弃草稿'), () => {
        const {chat} = open;
        (setup ? setups : answers).delete(chat.jobId);
        dialog.close();
        void refresh(chat);
      }));
  }

  async function next(last) {
    const context = open;
    const {chat, draft, kind} = context;
    if (draft.busy || draft.latest) return;
    if (!draft.single && draft.step < last) return go(1);
    draft.busy = true;
    draft.error = '';
    render();
    try {
      if (kind === 'setup') {
        const fields = {...draft.fields};
        if (fields.input_mode === 'none') for (const [key] of rejection) delete fields[key];
        const manifests = {};
        for (const name of namesFor(fields.input_mode)) {
          const file = draft.files[name];
          if (!file) continue;
          if (file.size > 256 * 1024) throw new Error(copy('The manifest exceeds 256 KiB.', '测试清单超过 256 KiB。'));
          manifests[name] = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(await file.arrayBuffer());
        }
        await live.setup(chat.jobId, {fields, manifests, reviewer_label: draft.reviewer.trim()}, draft.revision);
        setups.delete(chat.jobId);
      } else {
        const fields = effective(draft);
        const changed = changedAnswers(draft, fields);
        if (Object.keys(changed).length) {
          await live.save(chat.jobId, chat.typedReview ? changed : fields, draft.revision);
          if (chat.typedReview) chat.typedReviewEdited = true;
        } else {
          const latest = await live.job(chat.jobId);
          if (latest.contract.revision !== draft.revision ||
              (chat.typedReview && latest.contract.review_binding_digest !== draft.digest)) {
            draft.latest = latest;
            throw Object.assign(new Error(conflictLabel(locale())), {code: 'stale_revision'});
          }
        }
        answers.delete(chat.jobId);
      }
      await refresh(chat);
      if (open === context) dialog.close();
    } catch (error) {
      draft.error = error.message;
      if (isStaleReview(error) && !draft.latest) {
        try { draft.latest = await live.job(chat.jobId); } catch { /* A later retry can reload the current revision. */ }
      }
    } finally {
      draft.busy = false;
      if (open === context && dialog.open) {
        render();
        focusStep();
      }
      repaint();
    }
  }

  return {dialog, show, hasAnswers: chat => answers.has(chat.jobId)};
}

function typedBindingRows(contract, t) {
  return el('dl', {class: 'wizard-recap review-binding'}, bindingFields.map(([key, title]) => {
    const value = contract.review_binding?.[key];
    return el('div', {}, el('dt', {}, t(title)),
      el('dd', {}, typeof value === 'string' ? el('code', {}, value) :
        value?.label ? [el('strong', {}, value.label), value.summary ? ' · ' + value.summary : null] : '—'));
  }));
}

function typedAttachControl(chat, t, engine) {
  if (!canAttachTypedContract(chat)) return null;
  const file = el('input', {type: 'file', accept: '.json,application/json', hidden: true, onchange: () => {
    const picked = file.files[0];
    file.value = '';
    if (picked) void engine.attachContract(chat, picked);
  }});
  return [file, action(t('attachContract'), () => file.click(), {disabled: chat.attachingContract})];
}

export function guidedContractCard(chat, {t, locale, engine, wizard}) {
  if ((!chat.technicalSetup && !chat.typedReview) || !chat.contract) return null;
  const contract = chat.contract;
  const setup = chat.technicalSetup;
  const typed = chat.typedReview;
  const digest = contract.review_binding_digest;
  const language = locale === 'en' ? 0 : 1;
  const copy = (en, zh) => language ? zh : en;
  const readonly = Boolean(chat.execution?.run_id && !chat.execution.terminal) || chat.phase === 'cancelled';
  const values = savedAnswers(contract);
  const pending = wizard.hasAnswers(chat);
  const blockers = typed ? missingTypedFields(contract).map(([, title]) => t(title)) :
    setup.blockers.map(key => blockerLabels[key]?.[language] ?? key);
  const needsContract = typed && !contract.review_binding?.typed_contract_sha256 &&
    !contract.review_binding?.canonical_contract_sha256;
  const canEdit = !readonly && !needsContract && !chat.starting && !chat.startAcknowledged;
  const locked = pending || blockers.length > 0 || chat.reviewRefreshRequired ||
    chat.attachingContract || !digest;
  const reviewer = el('input', {
    class: 'reviewer-input', name: 'reviewer', required: true, maxlength: '128',
    autocomplete: 'off', value: chat.reviewer ?? '',
    oninput: event => { chat.reviewer = event.currentTarget.value; },
  });
  return el('section', {class: 'approval guided-contract', 'aria-label': t('approval')},
    el('div', {class: 'approval-title'},
      el('h2', {}, copy('Repair contract', '修复契约')),
      el('span', {class: 'wizard-muted'}, t('revision') + ' ' + contract.revision)),
    el('dl', {class: 'wizard-recap'}, questions.map(([key, name], index) => el('div', {},
      el('dt', {}, typed ? el('code', {}, canonicalLabels[key]) : name[language]),
      el('dd', {}, key === 'invalid_outcome' && noExternalInput(contract, setup)
        ? copy('Not applicable', '不适用') : values[key] || '—'),
      !canEdit || pending ? null : action(t('edit'), event => wizard.show(chat, 'answers', index, event.currentTarget),
        {'aria-label': t('edit') + ' ' + name[language]})))),
    typed ? typedBindingRows(contract, t) : null,
    needsContract ? el('p', {}, t('typedContractRequired')) : null,
    blockers.length ? el('div', {class: 'wizard-blockers'},
      el('strong', {}, copy('Required before approval', '审批前必须完成')),
      el('ul', {}, blockers.map(label => el('li', {}, label)))) : null,
    chat.reviewRefreshRequired ? el('div', {class: 'wizard-blockers'},
      el('p', {role: 'status'}, t('bindingChanged')),
      action(t(chat.reviewRefreshPending ? 'refresh' : 'bindingReviewed'), () => engine.reviewBinding(chat, digest))) : null,
    setup?.confirmed_by && !typed ? el('details', {},
      el('summary', {}, copy('Confirmed technical setup', '已确认的技术配置')),
      el('dl', {class: 'wizard-recap'},
        el('div', {}, el('dt', {}, copy('Repair target', '修复目标')), el('dd', {}, setup.fields.target_entry)),
        el('div', {}, el('dt', {}, copy('Execution environment', '运行环境')), el('dd', {}, setup.fields.environment)),
        el('div', {}, el('dt', {}, copy('Test manifest SHA-256', '测试清单 SHA-256')),
          el('dd', {}, el('code', {}, contract.review_binding.test_manifest_sha256))),
        el('div', {}, el('dt', {}, copy('Setup reviewer', '配置审阅人')), el('dd', {}, setup.confirmed_by)))) : null,
    el('div', {class: 'wizard-actions'},
      canEdit ? action(pending ? copy('Continue filling', '继续填写') : copy('Fill in contract', '填写契约'),
        event => wizard.show(chat, 'answers', undefined, event.currentTarget)) : null,
      !readonly && setup && !typed ? action(setup.confirmed_by ? copy('Review technical setup', '查看技术配置') :
        copy('Complete technical setup', '完成技术配置'), event => wizard.show(chat, 'setup', undefined, event.currentTarget)) : null,
      typed ? typedAttachControl(chat, t, engine) : null),
    chat.typedAttachmentError ? el('p', {class: 'wizard-error', role: 'alert'}, chat.typedAttachmentError) : null,
    chat.startAcknowledged ? el('p', {role: 'status'}, t('runAcknowledged')) : null,
    contract.approved ? el('div', {class: 'approval-complete'}, t('approved') + ' · ' + contract.reviewer_label,
      !readonly ? action(t('start'), () => engine.start(chat, digest), {
        ...primary, disabled: locked || chat.starting || Boolean(chat.startAcknowledged),
      }) : null)
      : !readonly ? el('form', {class: 'guided-approve', onsubmit: event => {
        event.preventDefault();
        void engine.decide(chat, chat.requestId, 'approve', null, reviewer.value.trim(), digest);
      }}, el('label', {class: 'reviewer-field'}, t('reviewer'), reviewer),
      action(t('approve'), null, {...primary, type: 'submit', disabled: locked})) : null);
}
