"""Self-contained MCP Apps HTML resources, shipped as package data. No
Python here - `importlib.resources.files("mis_mcp_runtime.ui")` reads the
`.html` files directly. Kept as a proper subpackage (this file) rather than a
bare directory so `importlib.resources` resolves it identically across the
Python versions this runtime supports."""
