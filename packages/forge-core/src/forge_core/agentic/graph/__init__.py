"""Deterministic evidence-mining and BusinessContext assembly for the
Context Discovery Agent.

Named `graph/` after the spec's §19 state-machine sketch. The bounded
workflow it asks for is real - a deterministic evidence pass, then a
LangChain `create_agent` session with a hard `recursion_limit`, then
assembly - but it is not expressed as a `StateGraph`. An earlier version
had one whose nodes returned `{}` and hardcoded `ready=True`, and which
nothing ever invoked; a decorative graph is worse than none, so it was
removed along with its `ContextDiscoveryState` TypedDict.
"""

from forge_core.agentic.graph.context_discovery_graph import (
    _analyze_structural_evidence,
    _build_business_context_model,
)

__all__ = [
    "_analyze_structural_evidence",
    "_build_business_context_model",
]
