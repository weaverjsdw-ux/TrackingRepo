# Pentera Agent Prompt Builder Design

## Goal

Create a focused, reusable prompt-builder artifact for Pentera work assistants. It should preserve the existing high-standard assistant behavior while making loops operational: each meaningful task must have a clear finish line, bounded retries, explicit checks, evidence, and escalation rules.

## Scope

This is a text-first prompt framework, not a software application. The first version should be easy to paste into an agent system prompt or adapt into task-specific prompts later.

In scope:

- A master Pentera work-assistant prompt.
- A compact prompt-builder decision rule for selecting the smallest useful loop set.
- Modular loops for intake, planning, execution, QA, watch/monitoring, reporting, research, email drafting, file/spreadsheet handling, and project follow-through.
- Approval gates for external actions, sensitive data, money, compliance, and irreversible changes.
- Verification and regression-proofing requirements.

Out of scope for this first version:

- A CLI, web app, or automated prompt generator.
- Vendor-specific agent orchestration code.
- Live email, upload, publishing, or credential actions.
- Large research bibliography or theory document.

## Design

The artifact is a single Markdown file at `docs/prompts/pentera-agent-prompt-builder.md`.

It has four layers:

1. Core identity and operating standard: defines the assistant role, confidentiality posture, quality priorities, and approval gates.
2. Prompt-builder selection rule: tells the agent to pick the smallest loop set required by the task instead of applying every loop every time.
3. Loop contracts: defines the fields every loop must carry, including objective, done condition, max attempts or timebox, evidence, retry rule, and blocker rule.
4. Task-specific loop library: provides concrete reusable loops for common Pentera work.

## Loop Quality Bar

Each loop must be executable by an agent, not merely aspirational. That means it must answer:

- What is the loop trying to finish?
- What evidence proves progress?
- When should the agent retry?
- When should the agent stop?
- What requires user approval?
- What should be reported back?

Loops should push agents toward verified completion but avoid runaway behavior. Every loop must have a maximum attempt count, a timebox, or a clear blocker condition.

## Error Handling

The prompt should require agents to stop and report rather than guess when:

- Required source data is missing or contradictory.
- A source is stale or untrusted.
- A file, count, recipient, or client identity is ambiguous.
- A live external action would occur without explicit approval.
- Sensitive Pentera/client data may be exposed.
- A loop hits its attempt or time boundary.

## Verification

Before considering the artifact complete, review it for:

- No placeholder markers or unfinished notes.
- No vague loop instructions without stop conditions.
- No instructions that encourage live sending, publishing, deletion, or credential use without approval.
- Clear distinction between drafts/previews/dry runs and live actions.
- A final reporting format that includes work done, evidence checked, blockers, risks, and next action.

## Success Criteria

The work is complete when:

- The prompt-builder artifact exists in `docs/prompts/pentera-agent-prompt-builder.md`.
- It contains a master prompt and modular loop templates.
- Each loop has explicit stop conditions and evidence requirements.
- The artifact is self-reviewed for ambiguity, contradictions, and missing approval gates.
- The files are committed to git.
