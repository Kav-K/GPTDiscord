"""Account routing for optional SDK integrations using shared or personal keys."""

from openai import AsyncOpenAI, OpenAI, omit

from services.environment_service import EnvService
from services.request_context import single_attempt_requests


class _RequestPolicy:
    def request(self, cast_to, options, **kwargs):
        # Enforce per-turn policy on clients constructed before REST recovery.
        if single_attempt_requests.get():
            options = options.model_copy(update={"max_retries": 0})
        return super().request(cast_to, options, **kwargs)


class RequestOpenAI(_RequestPolicy, OpenAI):
    pass


class RequestAsyncOpenAI(_RequestPolicy, AsyncOpenAI):
    pass


def client_routing(api_key):
    shared_key = api_key == EnvService.get_openai_token()
    # Empty account values suppress SDK environment fallbacks; omit removes
    # their headers from the final request instead of emitting empty strings.
    organization = (EnvService.get_openai_organization() or "") if shared_key else ""
    project = (EnvService.get_openai_project() or "") if shared_key else ""
    return {
        "organization": organization,
        "project": project,
        "default_headers": {
            "OpenAI-Organization": organization or omit,
            "OpenAI-Project": project or omit,
        },
    }
