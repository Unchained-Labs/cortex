"""MCP: cortex both consumes MCP servers as tools and exports its own."""

def sdk_available() -> bool:
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True
