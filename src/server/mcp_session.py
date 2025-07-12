"""
MCP session store for managing MCP server session state.

This module is focused specifically on MCP tool session management,
separate from HTTP conversation tracking concerns.
"""

from typing import Any


class McpSessionStore:
    """
    An in-memory store for MCP session data. This is now a no-op class
    as session management is handled by the agent's SQLite session.
    """

    def __init__(self):
        pass

    async def set_session_value(self, session_id: str, key: str, value: Any):
        pass

    async def get_session_value(self, session_id: str, key: str) -> Any:
        return None

    async def get_session_data(self, session_id: str) -> dict[str, Any]:
        return {}

    async def delete_session_value(self, session_id: str, key: str) -> bool:
        return False

    async def delete_session(self, session_id: str) -> bool:
        return False

    async def list_sessions(self) -> list[str]:
        return []
