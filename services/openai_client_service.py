"""Account routing for optional SDK integrations using shared or personal keys."""

from openai import omit

from services.environment_service import EnvService


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
