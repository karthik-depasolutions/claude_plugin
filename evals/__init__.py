"""P1-09 — the golden-question evaluation harness.

Everything else in Phase 1 is a hypothesis about quality until this measures
it: boots a generated plugin's real MCP server, drives a real model over its
real tools using the real SKILL.md as system prompt, and scores the
transcript against hand-computed ground truth. See harness/runner.py for the
entry point and datasets/*/questions.yaml for the golden sets.
"""
