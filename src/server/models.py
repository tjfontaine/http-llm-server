# src/server/models.py
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """
    Represents a single message in a conversation, including role, content, and
    timestamp.
    """

    role: str
    content: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ConversationHistory(BaseModel):
    """
    Represents the full conversation history for a session, containing a list of
    chat messages.
    """

    messages: list[ChatMessage] = Field(default_factory=list)

    def add_turn(self, role: str, content: str):
        """
        Adds a new message to the conversation history.

        Args:
            role: The role of the entity sending the message (e.g., 'user',
                'assistant').
            content: The text content of the message.
        """
        self.messages.append(ChatMessage(role=role, content=content))
