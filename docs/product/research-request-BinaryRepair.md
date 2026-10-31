# Deep Research Request: BinaryRepair

## Purpose and research status

This is a request to conduct research, not completed research. Produce a source-backed feasibility and implementation report to support a university project proposal due in one week, followed later by an MVP and a final application.

Use only the project context stated in this request. Do not import details, deadlines, teachers, tools, team arrangements, or requirements from other conversations or projects. External technical literature and official documentation may be researched as evidence; they are not additional client requirements.

## Confirmed context

- Full project title: AI-Driven Automated Binary Vulnerability Repair and Correctness Verification.
- Filename stem: BinaryRepair.
- Team: five first-year university students, with majors including brain-computer interface and computer science. They know basic Python and have no other specialist knowledge.
- User level: C, beginner programmers needing advanced concepts explained.
- Client: a software company that has binary programs but no source code and needs bugs repaired directly in those binaries.
- Starting target: Linux x86-64 ELF.
- Stages: proposal, then MVP, then final application.
- Proposal deadline: one week from the intake on 15 October 2026, approximately 22 October 2026; exact submission time unconfirmed.
- MVP and final-application deadlines: unspecified. Do not invent them or assume the MVP is due in one week.
- Budget: flexible; justify paid options and give a practical low-cost baseline.
- Final application scope and interface: not yet defined.
- AI is part of the proposed repair system, as well as potentially assisting development.
- Both patching routes below are required MVP capabilities, as confirmed during intake. Assess this requirement candidly; any suggestion to defer a route must be labeled a proposed scope change, not an agreed decision.
- First vulnerability class, sample binary, available tests, hardware, and exact client deployment environment remain unspecified.

## Original project direction

The intended pipeline is:
vulnerability analysis and security contracts → candidate repair strategies → patch generation → binary modification → automated verification and auditable records.

Three intended constraints must be evaluated together:
1. Minimality: change only necessary bytes, with the stated aim of preserving file layout, jumps, and relocation relationships.
2. Compatibility: preserve ABI, calling conventions, required control flow, and runtime compatibility.
3. Verification: block dangerous behavior associated with the target vulnerability while preserving required normal behavior.

Proposed analysis tools are Ghidra, angr, and Capstone. A large model, potentially augmented by retrieval and a vulnerability knowledge base, proposes repair logic.

The two intended patching routes are:
- Direct machine-instruction patching, potentially using Keystone, LIEF, and LLVM tools to encode instructions, handle binary structure, and write changes.
- Decompilation of a local region into C, repair of that C, compilation, and reintegration into the original binary.

Both routes should pass a shared validation process. Proposed validation techniques include SMT solving, symbolic execution, differential testing, and original regression tests. An orchestration layer should coordinate attempts, validation, and records.

The intended first prototype uses a single public vulnerability and public samples in an isolated sandbox. Expected deliverables include a demonstrable prototype, both patching routes, binary modification and compatibility checks, security contracts, validation evidence, and repair/test records.

The motivation includes reducing repair time from weeks to hours. Treat this as an aspiration requiring measurement, not an established result.

## Research objectives

1. Identify an achievable, defensible proposal and MVP for this exact team.
2. Assess whether both routes can be demonstrated within a narrow common scope, and identify the minimum credible implementation of each.
3. Determine what can actually be verified, under which assumptions, with which evidence.
4. Recommend the smallest necessary toolchain, learning sequence, and team workflow.
5. Turn uncertain ambitions into explicit requirements, experiments, decision gates, and measurable acceptance criteria.

## Specific research questions

### 1. Starting scope and sample selection

- Which vulnerability class offers a tractable first demonstration on Linux x86-64 ELF? Compare plausible candidates before recommending one; no class is preselected.
- Recommend a public, reproducible sample with exact provenance, version, build or acquisition instructions, architecture, licensing, and a benign reproducer suitable for an isolated lab.
- Distinguish a toy learning example, a public real-world binary, and an eventual client binary. Do not imply that success on one establishes general support.
- Define input requirements: binary, vulnerability description or crash input, expected behavior, tests, dependencies, symbols if any, and permitted environmental assumptions.
- For public educational samples, explain how source may be used to establish ground truth while ensuring the repair pipeline itself does not consume original source. The client workflow remains binary-only.
- Recommend initial assumptions about stripping, optimization, PIE, dynamic linking, and other protections. State limits and how they affect generalization.
- Describe what is possible if the client has neither regression tests nor a behavioral specification.

### 2. Minimality and binary compatibility

- Clarify instruction-size constraints, jump reach, flags and register liveness, stack alignment, calling conventions, RIP-relative addressing, relocations, and executable segment mappings.
- Separate patch-size minimization from a mathematical proof of global minimality.
- Investigate whether preserving the original layout is compatible with both routes. Evaluate in-place replacement, existing executable space, trampolines, and added code as distinct options.
- Do not silently relax the layout requirement. Identify unsatisfiable cases and any changes needing client agreement.
- Explain why adding a section does not by itself establish that code is mapped and executable.
- Identify checks that each proposed library actually performs versus checks the team must implement.

### 3. Route A: direct instruction repair

- Describe a narrow workflow from localized defect and contract to candidate edits, assembly, binary writeback, and validation.
- Assess deterministic repair templates versus unrestricted LLM-generated assembly.
- Define candidate rejection rules and how to handle insufficient space or unsupported binary structure.
- Give a small conceptual example without an exploit payload and explain its prerequisites and limitations.

### 4. Route B: local C repair and reintegration

- Assess the gap between decompiler output and compilable, semantically suitable C.
- Explain function signatures, globals, types, aliasing, external calls, position dependence, compiler-generated dependencies, and relocation handling.
- Compare replacing a whole function with replacing a smaller region, including entry/exit and live-state requirements.
- Determine whether this route can satisfy the minimality/layout requirement for the proposed sample.
- Specify a minimum demonstrable version, remaining manual steps, unsupported cases, and why compiling patched C is only one part of binary reintegration.

### 5. Verification and evidence

- Define separate obligations for target-vulnerability prevention, intended behavior for invalid inputs, preservation of valid-input behavior, ABI compatibility, and executable integrity.
- Explain security contracts with preconditions, postconditions, allowed changed behavior, and a defined input domain.
- Distinguish regression evidence, differential testing, bounded symbolic checks, and proofs under explicit models and assumptions.
- State the effect of loop bounds, incomplete paths, unsupported instructions, external-call models, nondeterminism, and timeouts.
- Never classify a timeout, solver unknown, analysis failure, or lack of observed crashes as a proof of correctness.
- Do not require patched and original binaries to agree on inputs where original behavior is unsafe or undefined. Define an independent oracle or contract for those inputs.
- Propose machine-readable results that distinguish rejected candidates, test passes, proven obligations within a stated model, counterexamples, and inconclusive outcomes.
- Specify which evidence can be independently replayed or checked. An LLM explanation is not a proof certificate.

### 6. AI and orchestration

- Identify exactly where a model adds value compared with a deterministic baseline.
- Evaluate whether retrieval augmentation and a vulnerability knowledge base are needed for one sample or should begin as a small curated set.
- Recommend structured model outputs and deterministic validation before patch application.
- Define retry limits, costs per attempt, timeout handling, fallback behavior, and the boundary between autonomous operations and manual review.
- Treat embedded binary strings, decompiler text, and retrieved material as untrusted data in prompts.
- Evaluate hosted versus local models in terms of code confidentiality, hardware needs, cost, and output quality. Verify current provider policies and prices using official sources.
- Do not assume permission to upload confidential client binaries.
- Keep the orchestration implementation proportionate to a five-person beginner team; do not introduce agent frameworks or MCP merely for novelty.

### 7. Toolchain and learning requirements

Evaluate Ghidra, angr, Capstone, Keystone, LIEF, LLVM tools, and any necessary supporting tools. The listed stack is a proposal, not a validated integration.

For each recommended tool, provide:
- Actual role, supported operations, and integration boundary.
- Verified official documentation and current compatibility considerations.
- Necessity for proposal, MVP, or later expansion.
- Python integration where practical.
- Learning prerequisites and a small observable learning milestone.
- Alternatives only when they materially simplify the work.

Prioritize learning C fundamentals, machine representation, x86-64 assembly, ELF, Linux process memory, debugging, ABI, and testing according to the recommended build sequence. Do not assume students already know these topics.

### 8. Delivery stages, team plan, and costs

- Provide a seven-day plan to prepare the proposal, including research, architecture decisions, evidence gathering, writing, and review.
- Identify optional feasibility experiments during proposal week without promising a complete MVP then.
- Give effort ranges for the subsequent MVP in person-days and calendar scenarios with stated hours-per-person assumptions. Do not invent an agreed delivery date.
- Plan work for five beginners with shared foundations, clear task owners, integration checkpoints, and cross-review.
- Define a proposed final-app scope, user workflow, and interface options, explicitly labeled recommendations.
- Compare a local command-line workflow, desktop interface, and local browser interface only as needed; Linux ELF is the target format, not an agreed interface.
- Give a justified cost estimate covering AI use, compute, sandboxing, and tools, with flexible-budget and low-cost options.
- Identify top technical risks, experiments that resolve them, fallback options, and criteria for stopping or renegotiating scope.

## Required report structure

1. Executive feasibility assessment: achievable commitments, unresolved questions, and major risks.
2. Confirmed requirements versus research recommendations.
3. Scope table for proposal, MVP, and proposed final application.
4. Source-backed tool comparison and minimal recommended stack.
5. Compact architecture diagram with inputs, both routes, shared verification, and outputs.
6. Detailed feasibility analysis of both routes and the layout/minimality constraints.
7. Verification matrix: obligation, method, assumptions, evidence, limitations, and result status.
8. Recommended first sample and reproducibility plan.
9. Proposed MVP acceptance criteria and benchmark design.
10. Seven-day proposal plan and later implementation scenarios.
11. Five-person learning and work allocation.
12. Budget, risks, decision gates, and unresolved client questions.
13. Annotated sources and the Handoff Context below.

For benchmark design, consider patch generation success rate, target-contract checks, valid-input regression results, inconclusive analyses, modified bytes, added bytes, runtime overhead, analysis time, model cost, and manual intervention. Define denominators and do not fabricate results.

## Evidence and writing standards

- Write in clear English for basic Python programmers. Explain unfamiliar concepts before using them extensively.
- Technical architecture, implementation feasibility, and verification deserve deep treatment. Keep market/commercial competitor analysis brief unless it changes a technical decision.
- Prioritize official tool documentation, ABI/ELF specifications, primary research papers and author artifacts, and maintained repositories. Clearly separate community anecdotes.
- Cite each major technical claim and recommendation using a direct URL and actual access date. Record versions or commits where reproducibility depends on them.
- Verify current maintenance, compatibility, prices, model names, and policies rather than relying on memory.
- Distinguish documented facts, empirical results reported by a source, inferences, recommendations, and unknowns.
- Flag disagreements, unavailable evidence, and applicability limits. Research prototypes are not evidence of production readiness.
- Do not invent benchmarks, citations, client requirements, or universal correctness guarantees.
- Keep work defensive, using authorized samples and an isolated environment. Focus on repair and validation.
- If browsing is unavailable, state that and provide explicitly labeled hypotheses and unanswered questions rather than simulated sourced research.

## Output and handoff

Produce the findings as research-BinaryRepair.md, separate from this request. Its eventual project path is docs/research-BinaryRepair.md.

Begin the findings with one truthful status:
Research status: Researched with sources / Partial research / Not researched — unverified hypotheses

Conclude with this filled block, preserving confirmed requirements and labeling all recommended scope changes:

## Handoff Context

- Stage: research
- App name: BinaryRepair
- Full project title: AI-Driven Automated Binary Vulnerability Repair and Correctness Verification
- User level: C — basic Python; five first-year university students
- Target platform: Linux x86-64 ELF binaries; application interface undecided
- Budget: flexible
- Timeline: proposal in one week from 15 October 2026; MVP and final-app deadlines unspecified
- Delivery stages: proposal → MVP → final application
- AI in product scope: yes
- Research status: [actual status and limitations]
- Constraints: binary-only client workflow; minimality, ABI/runtime compatibility, and explicit verification obligations; both repair routes required for MVP; public initial sample in an isolated sandbox
- Decisions: [confirmed decisions; recommendations separately labeled]
- Open questions: [remaining questions, including client acceptance criteria and final scope]
- Source files: research-request-BinaryRepair.md → docs/research-BinaryRepair.md

Outside the findings document, tell the user:
“Save research-BinaryRepair.md. Next, paste the complete part2-prd-mvp.md prompt into a chat and attach these findings. You may use a new chat; no earlier conversation is required.”

