# Pentera Agent Prompt Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a reusable Pentera prompt-builder artifact with operational, bounded loops.

**Architecture:** Add documentation-only files under `docs/`. Keep the final prompt-builder artifact separate from the design and implementation-plan records so it can be reused directly.

**Tech Stack:** Markdown, git.

---

## File Structure

- Create: `docs/superpowers/specs/2026-06-22-pentera-agent-prompt-builder-design.md`
  - Captures the approved design, scope, loop quality bar, and success criteria.
- Create: `docs/superpowers/plans/2026-06-22-pentera-agent-prompt-builder.md`
  - Captures this concise implementation plan.
- Create: `docs/prompts/pentera-agent-prompt-builder.md`
  - The reusable prompt-builder artifact.

### Task 1: Add Design And Plan Records

**Files:**
- Create: `docs/superpowers/specs/2026-06-22-pentera-agent-prompt-builder-design.md`
- Create: `docs/superpowers/plans/2026-06-22-pentera-agent-prompt-builder.md`

- [x] **Step 1: Write the approved design**

Document goal, scope, artifact structure, loop quality bar, error handling, verification, and success criteria.

- [x] **Step 2: Write this minimal implementation plan**

Document the files to create and the verification steps.

### Task 2: Add Prompt-Builder Artifact

**Files:**
- Create: `docs/prompts/pentera-agent-prompt-builder.md`

- [x] **Step 1: Create the master prompt**

Include assistant role, confidentiality posture, quality priorities, approval gates, and the prompt-builder selection rule.

- [x] **Step 2: Add loop contracts**

Define the required loop fields: objective, done condition, max attempts/timebox, checks, evidence, retry rule, blocker rule, and final status.

- [x] **Step 3: Add task-specific loop library**

Add focused loops for intake, planning, execution, QA, watch/monitoring, reporting, research, email drafting, file/spreadsheet handling, and project follow-through.

- [x] **Step 4: Add final response contract**

Require concise reporting of work done, verification evidence, blockers/risks, approvals needed, and next action when useful.

### Task 3: Verify And Commit

**Files:**
- Review: `docs/prompts/pentera-agent-prompt-builder.md`
- Review: `docs/superpowers/specs/2026-06-22-pentera-agent-prompt-builder-design.md`
- Review: `docs/superpowers/plans/2026-06-22-pentera-agent-prompt-builder.md`

- [x] **Step 1: Scan for placeholders**

Run a text search for placeholder markers and vague loop wording.

- [x] **Step 2: Check loop completeness**

Confirm every loop includes a stop condition and evidence requirement.

- [x] **Step 3: Check approval gates**

Confirm no live sending, publishing, deleting, overwriting, external sharing, credential use, compliance judgment, or money-related action is allowed without explicit approval.

- [x] **Step 4: Commit**

Commit the three Markdown files with message `docs: add pentera prompt builder`.
