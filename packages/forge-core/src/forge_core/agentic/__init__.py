"""Agentic reasoning layer: opt-in (`use_agent=True`), tool-using LangChain
agents for the pipeline steps where reasoning over real data (not just a
single-shot prompt) measurably improves accuracy. See `binding_agent.py`'s
docstring for the safety invariants this layer must preserve."""

from forge_core.agentic.binding_agent import propose_binding_with_agent

__all__ = ["propose_binding_with_agent"]
