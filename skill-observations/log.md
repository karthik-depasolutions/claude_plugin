# Skill observations

### Observation 1: Preserve data evidence in database-to-agent generation

**Status:** OPEN
**Date:** 2026-10-20
**Session context:** Reviewing and improving a database URL to Claude plugin generator after user corrected removal of sample-derived context.
**Skill:** New skill candidate: database-to-agent grounding review
**Type:** internal
**Phase/Area:** Profiling, synthesis, generation, and artifact validation

**Issue:** Removing sample rows, categories, value sets, and quality prose from profiling prompts broke the intended business-understanding path. The generator also had a gap between its sample-informed schema model and its mostly pack-driven skill and agent content.

**Suggested improvement:** For database-to-agent tasks, trace sample evidence from ingestion through model synthesis to generated instructions and executable tools. Verify the selected schema, relation coverage, cache invalidation, and whether synthesized facts actually reach each artifact before reducing prompt context.

**Principle:** Preserve evidence required for the product's core understanding step; control cost and privacy with bounded sampling, selective redaction, and explicit coverage rather than silently removing business context.

