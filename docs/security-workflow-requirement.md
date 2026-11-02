# Security Workflow Implementation Required

**Status:** Awaiting human action  
**Issue:** [#463](https://github.com/kellyaa/agent-newsletter/issues/463)  
**Date:** 2026-11-02

## Summary

CI security scanning is required: CodeQL, `pip-audit`, and `pnpm audit` are not currently active. This document captures the implementation requirement since AI agents cannot directly edit `.github/workflows/**` per SECURITY-AI.md.

## What needs to be done

Create a new file `.github/workflows/security.yml` with the exact content specified in issue #463.

## Key points from the issue

- **CodeQL**: Static analysis for Python and JavaScript/TypeScript
- **pip-audit**: Audit Python dependencies from locked requirements
- **pnpm-audit**: Audit Node.js site dependencies (high-severity only)
- **Scheduling**: Weekly on Monday, plus on pull requests and pushes to main
- **Least-privilege permissions**: Only CodeQL job gets `security-events: write`

## Notes

1. Use the exact YAML from issue #463 as the source of truth
2. Pin CodeQL actions to SHAs (currently use `v3` tag; update to commit SHA)
3. Copy `pnpm/action-setup` and `setup-node` configuration from `site-tests.yml`
4. Consider making `pip-audit` and `pnpm-audit` non-blocking (`continue-on-error: true`) for the first week to monitor for issues

## Reference

- SECURITY-AI.md: Details on agent permissions and restrictions
- AGENTS.md: Rules for AI contributions (line 36)
- Issue #463: Complete workflow specification
