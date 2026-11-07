"""
MCP Financial Action Server.
Transactional and Asset-Modifying MCP Server for Banking Actions.
"""

from mcp_financial_action_server.server import app, mcp

__all__ = ["mcp", "app"]
