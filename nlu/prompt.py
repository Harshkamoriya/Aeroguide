# nlu/prompt.py — re-exports NLU prompts from the central prompt library.
from agent.prompt import (
    INTENT_SYSTEM_PROMPT,
    INTENT_USER_PROMPT,
    ENTITY_SYSTEM_PROMPT,
    ENTITY_USER_PROMPT,
)

__all__ = [
    "INTENT_SYSTEM_PROMPT",
    "INTENT_USER_PROMPT",
    "ENTITY_SYSTEM_PROMPT",
    "ENTITY_USER_PROMPT",
]
