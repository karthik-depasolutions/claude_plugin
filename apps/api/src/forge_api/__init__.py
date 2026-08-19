"""forge-api — FastAPI orchestration service for Data2plugin
generator. Wraps `forge_core.orchestrator.run_pipeline` with job persistence,
SSE progress streaming, and HTTP endpoints for the CLI-equivalent workflow
(connect source -> confirm industry -> review bindings -> download/publish).
"""
