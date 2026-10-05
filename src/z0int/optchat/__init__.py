"""OptChat log and summary tree. The chat history is the memory.

This is not a second memory database. Continuity, TencentDB, and the
bridge keep their jobs. OptChat only appends this harness's own log.
"""

from .log import ChatLog, append_message, load_chat

__all__ = ["ChatLog", "append_message", "load_chat"]
