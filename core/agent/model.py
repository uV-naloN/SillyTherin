"""The OpenRouter chat model for one message, with that message's model and thinking settings."""

from http import HTTPStatus

from langchain_openrouter import ChatOpenRouter
from openrouter.errors import BadRequestResponseError

from core.configuration.config import OPENROUTER_API_KEY, OPENROUTER_URL
from core.contracts.chat import ModelRequest

APP_TITLE = "SillyTherin"  # how OpenRouter names the app on the account's activity page
OPENROUTER_RELAY_ERROR_MESSAGE = "Request path cannot be relayed"


def build_chat_model(request: ModelRequest) -> ChatOpenRouter:
    """The model for one message; no app URL is sent, so OpenRouter only learns the app's name."""
    return ChatOpenRouter(
        base_url=OPENROUTER_URL,
        model=request.model,
        streaming=True,
        reasoning=request.thinking.get("reasoning"),
        openrouter_api_key=OPENROUTER_API_KEY,
        app_title=APP_TITLE,
        app_url=None,
    )


def is_openrouter_relay_failure(error: Exception) -> bool:
    if not isinstance(error, BadRequestResponseError) or error.status_code != HTTPStatus.BAD_REQUEST:
        return False
    response_error = error.data.error
    return response_error.code == HTTPStatus.BAD_REQUEST and response_error.message == OPENROUTER_RELAY_ERROR_MESSAGE
