"""Installed metadata remains readable without sharing source credentials."""

from app.schemas.plugin import InstalledPluginListResponse
from app.services.plugin_runtime_client import JsonObject


def installed_plugin_catalog(
    data: JsonObject,
    *,
    include_source: bool,
) -> InstalledPluginListResponse:
    result = InstalledPluginListResponse.model_validate(data)
    if include_source:
        return result
    return result.model_copy(
        update={
            "plugins": [
                plugin.model_copy(update={"source_url": None})
                for plugin in result.plugins
            ],
        }
    )
