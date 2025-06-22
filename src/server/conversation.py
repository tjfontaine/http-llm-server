"""
HTTP conversation store for tracking request/response pairs.

This module is focused specifically on HTTP server conversation tracking,
separate from MCP session management concerns.
"""

import asyncio
import json
import logging
import os
from weakref import WeakValueDictionary

from .models import ConversationHistory


class HttpConversationStore:
    """
    HTTP-focused conversation store for tracking request/response pairs.

    Optimized for HTTP server middleware lifecycle and conversation tracking.
    Uses per-session locking to prevent contention between different sessions.
    """

    def __init__(self, save_to_disk: bool = True):
        self._histories: dict[str, ConversationHistory] = {}
        self._token_counts: dict[str, int] = {}
        # Per-session locks - use WeakValueDictionary for automatic cleanup
        self._session_locks: WeakValueDictionary[str, asyncio.Lock] = (
            WeakValueDictionary()
        )
        # Lock for managing the session locks dictionary itself
        self._locks_manager_lock = asyncio.Lock()
        self._save_to_disk = save_to_disk
        # Get loggers for consistent logging throughout the application
        self._logger = logging.getLogger("llm_http_server_app")
        self._conversation_logger = logging.getLogger("conversation_history")

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a lock for the given session ID."""
        # Check if lock already exists (no manager lock needed for read)
        if session_id in self._session_locks:
            return self._session_locks[session_id]

        # Need to create a new lock - acquire manager lock
        async with self._locks_manager_lock:
            # Double-check pattern - another task might have created it
            if session_id in self._session_locks:
                return self._session_locks[session_id]

            # Create new lock for this session
            new_lock = asyncio.Lock()
            self._session_locks[session_id] = new_lock
            self._logger.debug(
                f"Created new HTTP conversation lock for session '{session_id}'"
            )
            return new_lock

    async def get_history(self, session_id: str) -> ConversationHistory:
        """Retrieve the conversation history for a given session ID."""
        session_lock = await self._get_session_lock(session_id)
        async with session_lock:
            return self._histories.get(session_id, ConversationHistory())

    async def record_turn(self, session_id: str, role: str, text_content: str) -> None:
        """Record a new turn in the conversation history for a given session ID."""
        if not session_id:
            self._logger.warning(
                "Attempted to record HTTP conversation turn without a session_id. Turn not stored."
            )
            return

        session_lock = await self._get_session_lock(session_id)
        async with session_lock:
            history = self._histories.setdefault(session_id, ConversationHistory())
            history.add_message(role=role, content=text_content)
            self._conversation_logger.info(
                f"Recorded '{role}' turn for session_id '{session_id}' in HTTP conversation store. Total turns: {len(history.messages)}"
            )

    async def replace_history(
        self, session_id: str, history: ConversationHistory
    ) -> None:
        """Replace the entire conversation history for a given session ID."""
        if not session_id:
            self._logger.warning(
                "Attempted to replace HTTP conversation history without a session_id. History not replaced."
            )
            return

        session_lock = await self._get_session_lock(session_id)
        async with session_lock:
            self._histories[session_id] = history
            self._token_counts[session_id] = 0  # Reset token count
            self._logger.info(
                f"Replaced HTTP conversation history for session '{session_id}'. New history has {len(history.messages)} turn(s). Token count reset."
            )

    async def get_token_count(self, session_id: str) -> int:
        """Retrieve the token count for the last known state of the history."""
        session_lock = await self._get_session_lock(session_id)
        async with session_lock:
            return self._token_counts.get(session_id, 0)

    async def update_token_count(self, session_id: str, count: int) -> None:
        """Update the token count for the session's history."""
        if not session_id:
            return

        session_lock = await self._get_session_lock(session_id)
        async with session_lock:
            self._token_counts[session_id] = count
            self._logger.info(
                f"Updated token count for session '{session_id}' to {count} in HTTP conversation store."
            )

    async def save_all_sessions_on_shutdown(self, log_directory: str) -> None:
        """Save all current session histories to files on server shutdown."""
        if not self._save_to_disk:
            self._logger.info(
                "HTTP conversation saving to disk is disabled. Skipping save_all_sessions_on_shutdown."
            )
            return

        try:
            os.makedirs(log_directory, exist_ok=True)
            self._logger.info(
                f"Ensured HTTP conversation log directory exists: {log_directory}"
            )

            # For shutdown, we need to acquire all session locks to ensure consistency
            # First, get a snapshot of all session IDs
            async with self._locks_manager_lock:
                session_ids = list(self._histories.keys())

            # Acquire locks for all sessions in sorted order to avoid deadlocks
            session_locks = []
            for session_id in sorted(session_ids):
                session_lock = await self._get_session_lock(session_id)
                session_locks.append((session_id, session_lock))

            # Acquire all locks in order
            acquired_locks = []
            try:
                for session_id, session_lock in session_locks:
                    await session_lock.acquire()
                    acquired_locks.append(session_lock)

                # Now we have exclusive access to all sessions
                if not self._histories:
                    self._logger.info("No HTTP conversation histories to save.")
                    return

                saved_count = 0
                for session_id, history_obj in self._histories.items():
                    if not history_obj.messages:
                        continue
                    file_path = os.path.join(log_directory, f"{session_id}.json")
                    try:
                        # Using standard open for simplicity; can be replaced with aiofiles if it becomes a bottleneck.
                        with open(file_path, "w") as f:
                            json.dump(history_obj.model_dump(), f, indent=2)
                        self._logger.info(
                            f"HTTP conversation history for session '{session_id}' saved to {file_path}"
                        )
                        saved_count += 1
                    except Exception as e:
                        self._logger.exception(
                            f"Failed to save HTTP conversation history for session '{session_id}' to {file_path}: {e}"
                        )
                if saved_count > 0:
                    self._logger.info(
                        f"Successfully saved {saved_count} HTTP conversation histories."
                    )
                else:
                    self._logger.info(
                        "No non-empty HTTP conversation histories were saved."
                    )
            finally:
                # Release all acquired locks in reverse order
                for session_lock in reversed(acquired_locks):
                    session_lock.release()

        except Exception as e:
            self._logger.exception(
                f"Failed to create/access HTTP conversation log directory '{log_directory}': {e}"
            )

        finally:
            self._logger.info("HTTP conversation saving to disk completed.")
