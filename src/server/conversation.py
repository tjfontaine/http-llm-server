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

from src.logging_config import get_loggers
from src.server.models import ChatMessage, ConversationHistory

_conversation_logger, _, _ = get_loggers()


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

    async def record_turn(self, session_id: str, turn: ChatMessage):
        """Records a single turn in the conversation history for a given session."""
        if not session_id:
            self._logger.warning(
                "Attempted to record turn without session_id. Not stored."
            )
            return

        with self._lock:
            history = self._get_or_create_history(session_id)
            history.add_turn(role=turn.role, content=turn.content)
            self._logger.info(
                "Recorded turn for session '%s'. Total turns: %d",
                session_id,
                len(history.messages),
            )

    async def replace_history(self, session_id: str, history: ConversationHistory):
        """Replaces the entire conversation history for a given session."""
        if not session_id:
            self._logger.warning(
                "Attempted to replace history without session_id. Not replaced."
            )
            return

        with self._lock:
            self._histories[session_id] = history
            # Reset the token count since history is being overwritten
            if session_id in self._token_counts:
                del self._token_counts[session_id]
            self._logger.info(
                "Replaced history for session '%s'. New history has %d turn(s). "
                "Token count reset.",
                session_id,
                len(history.messages),
            )

    async def get_token_count(self, session_id: str) -> int:
        """Retrieve the token count for the last known state of the history."""
        session_lock = await self._get_session_lock(session_id)
        async with session_lock:
            return self._token_counts.get(session_id, 0)

    async def update_token_count(self, session_id: str, count: int):
        """Updates the token count for a given session."""
        if not session_id:
            return
        with self._lock:
            self._token_counts[session_id] = count
            self._logger.info(
                "Updated token count for session '%s' to %d in store.",
                session_id,
                count,
            )

    async def save_all_sessions_on_shutdown(self, log_directory: str) -> None:
        """Saves all conversation histories to disk."""
        if not self._save_to_disk:
            self._logger.info(
                "Conversation saving to disk is disabled. Skipping shutdown save."
            )
            return

        try:
            os.makedirs(log_directory, exist_ok=True)
            with self._lock:
                saved_count = 0
                for session_id, history_obj in self._histories.items():
                    file_path = os.path.join(log_directory, f"{session_id}.json")
                    try:
                        # Using standard open; can be replaced with aiofiles
                        # if it becomes a bottleneck.
                        with open(file_path, "w") as f:
                            json.dump(history_obj.model_dump(), f, indent=2)
                        self._logger.info(
                            "HTTP conversation for session '%s' saved to %s",
                            session_id,
                            file_path,
                        )
                        saved_count += 1
                    except Exception as e:
                        self._logger.exception(
                            "Failed to save history for session '%s' to %s: %s",
                            session_id,
                            file_path,
                            e,
                        )
                if saved_count > 0:
                    self._logger.info(
                        f"Successfully saved {saved_count} HTTP conversation(s)."
                    )
        except Exception as e:
            self._logger.exception(
                "Failed to create/access conversation log directory '%s': %s",
                log_directory,
                e,
            )

    def _get_or_create_history(self, session_id: str) -> "ConversationHistory":
        """Helper to get or create a history for a given session ID."""
        history = self._histories.get(session_id)
        if history is None:
            history = ConversationHistory()
            self._histories[session_id] = history
        return history
