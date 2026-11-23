"""Approval prompts shared by the tools: the question, its choices and the timeout."""

from sillytherin.chat.current import this_chat
from sillytherin.chat.ports import Choice

APPROVAL_TIMEOUT_SECONDS = 120

RUN_CHOICE = Choice("run", "Run")
SKIP_CHOICE = Choice("skip", "Skip")
DELETE_CHOICE = Choice("delete", "Delete")
KEEP_CHOICE = Choice("keep", "Keep")
DOWNLOAD_CHOICE = Choice("download", "Download")
DOWNLOAD_AGAIN_CHOICE = Choice("download_again", "Download again")

RUN_OR_SKIP = (RUN_CHOICE, SKIP_CHOICE)
DELETE_OR_KEEP = (DELETE_CHOICE, KEEP_CHOICE)
DOWNLOAD_OR_SKIP = (DOWNLOAD_CHOICE, SKIP_CHOICE)
DOWNLOAD_AGAIN_OR_SKIP = (DOWNLOAD_AGAIN_CHOICE, SKIP_CHOICE)


async def ask_choice(question: str, choices: tuple[Choice, ...], accepted: Choice) -> bool:
    """Show the question with its choices; True only when the user picks `accepted`."""
    answer = await this_chat().asker.ask_choice(question, choices, APPROVAL_TIMEOUT_SECONDS)
    return answer == accepted.id
