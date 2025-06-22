"""
MCP session store for managing MCP server session state.

This module is focused specifically on MCP tool session management,
separate from HTTP conversation tracking concerns.
"""

import asyncio
import logging
from typing import Any


class McpSessionStore:
    """
    MCP-focused session store for managing MCP server session state.

    Simplified key-value storage optimized for MCP tool operations.
    Separate from HTTP conversation tracking to avoid coupling and lock contention.
    """

    def __init__(self):
        self._session_data: dict[str, dict[str, Any]] = {}
        # Simple global lock for MCP operations - these are typically lightweight
        self._lock = asyncio.Lock()
        self._logger = logging.getLogger("llm_http_server_app")

    async def get_session_data(self, session_id: str) -> dict[str, Any]:
        """Get all session data for the given session ID."""
        async with self._lock:
            return self._session_data.get(session_id, {}).copy()

    async def set_session_data(self, session_id: str, data: dict[str, Any]) -> None:
        """Set session data for the given session ID."""
        if not session_id:
            self._logger.warning(
                "Attempted to set MCP session data without a session_id."
            )
            return

        async with self._lock:
            self._session_data[session_id] = data.copy()
            self._logger.debug(
                f"Set MCP session data for session '{session_id}' with {len(data)} keys"
            )

    async def get_session_value(self, session_id: str, key: str) -> Any:
        """Get a specific value from session data."""
        async with self._lock:
            session_data = self._session_data.get(session_id, {})
            return session_data.get(key)

    async def set_session_value(self, session_id: str, key: str, value: Any) -> None:
        """Set a specific value in session data."""
        if not session_id:
            self._logger.warning(
                "Attempted to set MCP session value without a session_id."
            )
            return

        async with self._lock:
            if session_id not in self._session_data:
                self._session_data[session_id] = {}
            self._session_data[session_id][key] = value
            self._logger.debug(
                f"Set MCP session value for session '{session_id}': {key} = {value}"
            )

    async def delete_session_value(self, session_id: str, key: str) -> bool:
        """Delete a specific value from session data. Returns True if key existed."""
        if not session_id:
            return False

        async with self._lock:
            session_data = self._session_data.get(session_id, {})
            if key in session_data:
                del session_data[key]
                self._logger.debug(
                    f"Deleted MCP session value for session '{session_id}': {key}"
                )
                return True
            return False

    async def delete_session(self, session_id: str) -> bool:
        """Delete all session data for the given session ID. Returns True if session existed."""
        if not session_id:
            return False

        async with self._lock:
            if session_id in self._session_data:
                del self._session_data[session_id]
                self._logger.debug(
                    f"Deleted MCP session data for session '{session_id}'"
                )
                return True
            return False

    async def list_sessions(self) -> list[str]:
        """List all session IDs that have MCP session data."""
        async with self._lock:
            return list(self._session_data.keys())
