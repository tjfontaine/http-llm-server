import abc
import asyncio
import json
import logging
import os

from .models import ConversationHistory


class AbstractSessionStore(abc.ABC):
    """Abstract base class for session storage."""

    @abc.abstractmethod
    async def get_history(self, session_id: str) -> ConversationHistory:
        """Retrieve the conversation history for a given session ID."""
        pass

    @abc.abstractmethod
    async def record_turn(self, session_id: str, role: str, text_content: str) -> None:
        """Record a new turn in the conversation history for a given session ID."""
        pass

    @abc.abstractmethod
    async def replace_history(
        self, session_id: str, history: ConversationHistory
    ) -> None:
        """Replace the entire conversation history for a given session ID."""
        pass

    @abc.abstractmethod
    async def get_token_count(self, session_id: str) -> int:
        """Retrieve the token count for the last known state of the history."""
        pass

    @abc.abstractmethod
    async def update_token_count(self, session_id: str, count: int) -> None:
        """Update the token count for the session's history."""
        pass

    @abc.abstractmethod
    async def save_all_sessions_on_shutdown(self, log_directory: str) -> None:
        """Save all current session histories, typically on server shutdown."""
        pass


class InMemorySessionStore(AbstractSessionStore):
    """In-memory implementation of the session store."""

    def __init__(self, save_to_disk: bool = True):
        self._histories: dict[str, ConversationHistory] = {}
        self._token_counts: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._save_to_disk = save_to_disk
        # Get loggers for consistent logging throughout the application
        self._logger = logging.getLogger("llm_http_server_app")
        self._conversation_logger = logging.getLogger("conversation_history")

    async def get_history(self, session_id: str) -> ConversationHistory:
        """Retrieve the conversation history for a given session ID."""
        async with self._lock:
            return self._histories.get(session_id, ConversationHistory())

    async def record_turn(self, session_id: str, role: str, text_content: str) -> None:
        """Record a new turn in the conversation history for a given session ID."""
        if not session_id:
            self._logger.warning(
                "Attempted to record conversation turn without a session_id in InMemorySessionStore. Turn not stored."
            )
            return
        async with self._lock:
            history = self._histories.setdefault(session_id, ConversationHistory())
            history.add_message(role=role, content=text_content)
            self._conversation_logger.info(
                f"Recorded '{role}' turn for session_id '{session_id}' via InMemorySessionStore. Total turns: {len(history.messages)}"
            )

    async def replace_history(
        self, session_id: str, history: ConversationHistory
    ) -> None:
        """Replace the entire conversation history for a given session ID."""
        if not session_id:
            self._logger.warning(
                "Attempted to replace conversation history without a session_id in InMemorySessionStore. History not replaced."
            )
            return
        async with self._lock:
            self._histories[session_id] = history
            self._token_counts[session_id] = 0  # Reset token count
            self._logger.info(
                f"Replaced history for session '{session_id}'. New history has {len(history.messages)} turn(s). Token count reset."
            )

    async def get_token_count(self, session_id: str) -> int:
        """Retrieve the token count for the last known state of the history."""
        async with self._lock:
            return self._token_counts.get(session_id, 0)

    async def update_token_count(self, session_id: str, count: int) -> None:
        """Update the token count for the session's history."""
        if not session_id:
            return
        async with self._lock:
            self._token_counts[session_id] = count
            self._logger.info(
                f"Updated token count for session '{session_id}' to {count}."
            )

    async def save_all_sessions_on_shutdown(self, log_directory: str) -> None:
        """Save all current session histories to files on server shutdown."""
        if not self._save_to_disk:
            self._logger.info(
                "Conversation saving to disk is disabled. Skipping save_all_sessions_on_shutdown (via InMemorySessionStore)."
            )
            return

        try:
            os.makedirs(log_directory, exist_ok=True)
            self._logger.info(
                f"Ensured conversation log directory exists: {log_directory} (via InMemorySessionStore)"
            )

            async with self._lock:  # Ensure exclusive access for saving
                if not self._histories:
                    self._logger.info(
                        "No conversation histories to save (via InMemorySessionStore)."
                    )
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
                            f"Conversation history for session '{session_id}' saved to {file_path} (via InMemorySessionStore)"
                        )
                        saved_count += 1
                    except Exception as e:
                        self._logger.exception(
                            f"Failed to save conversation history for session '{session_id}' to {file_path} (via InMemorySessionStore): {e}"
                        )
                if saved_count > 0:
                    self._logger.info(
                        f"Successfully saved {saved_count} session histories (via InMemorySessionStore)."
                    )
                else:
                    self._logger.info(
                        "No non-empty session histories were saved (via InMemorySessionStore)."
                    )
        except Exception as e:
            self._logger.exception(
                f"Failed to create/access conversation log directory '{log_directory}' in InMemorySessionStore: {e}"
            )

        finally:
            self._logger.info(
                "Conversation saving to disk completed (via InMemorySessionStore)."
            )
