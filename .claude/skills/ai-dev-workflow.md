---
name: ai-dev-workflow
description: Systematic AI-assisted development workflow for shipping features faster. Use when building features, implementing code, or establishing development practices that leverage AI tools effectively. Covers the full cycle from ideation through deployment with appropriate human checkpoints.
---

# AI-Assisted Development Workflow

A four-phase workflow for AI-augmented feature development that maintains code quality while accelerating delivery.

## Workflow Overview

1. **Scaffold** → Generate rough structure with Claude Code (target ~70% complete)
2. **Refine** → Watch AI write code in real-time in IDE (e.g., Antigravity), catch issues immediately
3. **Review** → Use AI tools to review AI-generated code (catches different issue types)
4. **Test & Deploy** → Human-controlled staging and deployment decisions

## Phase 1: Scaffolding with Claude Code

Use Claude Code in terminal for initial feature structure:

- Describe what to build in natural language
- Let AI generate rough architecture and boilerplate
- Target 70% completion—don't optimize prematurely
- Focus on getting the structure right, not implementation details

**Exit criteria:** Working rough structure that compiles/runs.

## Phase 2: Real-Time Refinement in IDE

Switch to an AI-enabled IDE (e.g., Antigravity IDE) for iterative development:

- Watch code generation in real-time (key advantage over batch dumps)
- Catch hallucinations and errors immediately as they appear
- Make corrections inline rather than reviewing massive diffs later
- Iterate quickly on implementation details

**Exit criteria:** Feature functionally complete, code reasonably clean.

## Phase 3: AI-Assisted Code Review

Use AI review tools to catch issues humans might miss:

- **Quick checks:** CodeRabbit VSCode extension for immediate feedback
- **Deep analysis:** Push to PR for CodeRabbit GitHub app review

AI reviewing AI-generated code catches different issue categories than human review or the generating AI itself. This is not redundant—different models and review contexts surface different problems.

**Exit criteria:** PR passes AI review with no critical issues.

## Phase 4: Human Testing & Deployment

Testing and deployment remain human-controlled:

- Run comprehensive test suites in staging
- AI can help write tests, but humans verify coverage
- Deployment decisions stay with humans
- No AI-autonomous production deployments

**Exit criteria:** Tests pass, human approves deployment.

## Key Principles

- **AI handles repetitive implementation** → Engineers focus on system design and code quality
- **Real-time > batch review** → Watching code generate catches issues faster than reviewing dumps
- **AI reviews AI** → Not redundant; catches complementary issue types
- **Humans own deployment** → AI accelerates; humans decide

## Expected Outcomes

- ~40% faster feature shipping
- Engineers focus on architecture over boilerplate
- Junior engineers deliver senior-level output by focusing on design while AI handles implementation
