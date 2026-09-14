from typing import Any

from dify_plugin import ToolProvider


class WeatherProvider(ToolProvider):
    """Open-Meteo is public, so this sample has no credentials to validate."""

    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        return
