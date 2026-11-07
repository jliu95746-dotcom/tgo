import re
import time
from urllib.parse import urljoin, urlparse
import httpx
import yaml
from typing import Optional, Dict, Any
from app.core.logging import get_logger
from app.services.plugin_failures import log_plugin_failure
from app.domain.business_tools.target_transport import business_query_log_scope

logger = get_logger("services.url_resolver")


class PluginURLResolver:
    """Resolves plugin URL and fetches plugin.yml configuration."""

    GITHUB_PATTERN = re.compile(
        r"^https?://github\.com/([^/]+)/([^/]+)(?:/tree/([^/]+)(?:/(.*))?)?/?$"
    )
    GITHUB_BLOB_PATTERN = re.compile(
        r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)$"
    )
    GITEE_PATTERN = re.compile(
        r"^https?://gitee\.com/([^/]+)/([^/]+)(?:/tree/([^/]+)(?:/(.*))?)?/?$"
    )
    GITEE_BLOB_PATTERN = re.compile(
        r"^https?://gitee\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)$"
    )

    async def resolve(self, url: str) -> Dict[str, Any]:
        (
            "\n        Resolve URL and get plugin.yml content.\n        Return"
            "s a dictionary containing the resolved configuration with absolut"
            "e URLs for sources.\n        "
        )
        url = url.strip()

        # 1. Try GitHub Blob URL
        if blob_match := self.GITHUB_BLOB_PATTERN.match(url):
            return await self._resolve_github_blob(blob_match)

        # 2. Try Gitee Blob URL
        if gitee_blob_match := self.GITEE_BLOB_PATTERN.match(url):
            return await self._resolve_gitee_blob(gitee_blob_match)

        # 3. Try GitHub Repo/Tree URL
        if github_match := self.GITHUB_PATTERN.match(url):
            return await self._resolve_github(github_match)

        # 4. Try Gitee Repo/Tree URL
        if gitee_match := self.GITEE_PATTERN.match(url):
            return await self._resolve_gitee(gitee_match)

        # 5. Custom URL
        return await self._resolve_custom(url)

    async def _resolve_github_blob(self, match) -> Dict[str, Any]:
        user, repo, branch, path = match.groups()
        # Convert GitHub blob URL to raw URL. Use refs/heads/ if branch is
        # provided for robustness.
        raw_url = (
            f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
        )
        base_url = raw_url.rsplit("/", 1)[0] + "/"

        config = await self._try_fetch_yaml(raw_url)
        if config:
            return self._resolve_relative_urls(config, base_url)

        raise ValueError("Could not fetch or parse plugin configuration")

    async def _resolve_gitee_blob(self, match) -> Dict[str, Any]:
        user, repo, branch, path = match.groups()
        # Convert Gitee blob URL to raw URL
        raw_url = f"https://gitee.com/{user}/{repo}/raw/{branch}/{path}"
        base_url = raw_url.rsplit("/", 1)[0] + "/"

        config = await self._try_fetch_yaml(raw_url)
        if config:
            return self._resolve_relative_urls(config, base_url)

        raise ValueError("Could not fetch or parse plugin configuration")

    async def _resolve_github(self, match) -> Dict[str, Any]:
        user, repo, branch, path = match.groups()
        branches = [branch] if branch else ["main", "master"]
        path = path.strip("/") if path else ""

        for br in branches:
            base_url = f"https://raw.githubusercontent.com/{user}/{repo}/{br}/"
            if path:
                base_url = urljoin(base_url, path + "/")

            config = await self._fetch_yaml_with_fallbacks(base_url)
            if config:
                return self._resolve_relative_urls(config, base_url)

        raise ValueError(
            (
                "Could not find plugin.yml or plugin.yaml in the GitHub reposi"
                "tory."
            )
        )

    async def _resolve_gitee(self, match) -> Dict[str, Any]:
        user, repo, branch, path = match.groups()
        branches = [branch] if branch else ["master", "main"]
        path = path.strip("/") if path else ""

        for br in branches:
            base_url = f"https://gitee.com/{user}/{repo}/raw/{br}/"
            if path:
                base_url = urljoin(base_url, path + "/")

            config = await self._fetch_yaml_with_fallbacks(base_url)
            if config:
                return self._resolve_relative_urls(config, base_url)

        raise ValueError(
            "Could not find plugin.yml or plugin.yaml in the Gitee repository."
        )

    async def _resolve_custom(self, url: str) -> Dict[str, Any]:
        """Handle custom URLs."""
        parsed = urlparse(url)
        path = parsed.path

        # If it points directly to a YAML file
        if path.endswith((".yml", ".yaml")):
            yaml_url = url
            base_url = url.rsplit("/", 1)[0] + "/"
            config = await self._try_fetch_yaml(yaml_url)
            if config:
                return self._resolve_relative_urls(config, base_url)
        else:
            # Directory URL, auto-append plugin.yml
            base_url = url if url.endswith("/") else url + "/"
            config = await self._fetch_yaml_with_fallbacks(base_url)
            if config:
                return self._resolve_relative_urls(config, base_url)

        raise ValueError(
            (
                "Could not find or parse plugin.yml/plugin.yaml at the provide"
                "d URL."
            )
        )

    async def _fetch_yaml_with_fallbacks(
        self, base_url: str
    ) -> Optional[Dict[str, Any]]:
        """Try fetching plugin.yml then plugin.yaml."""
        for filename in ["plugin.yml", "plugin.yaml"]:
            yaml_url = urljoin(base_url, filename)
            config = await self._try_fetch_yaml(yaml_url)
            if config:
                return config
        return None

    async def _try_fetch_yaml(self, url: str) -> Optional[Dict[str, Any]]:
        """Attempt to fetch and parse a YAML file."""
        # Bypass cache for GitHub/Gitee by adding a timestamp
        if "raw.githubusercontent.com" in url or "gitee.com" in url:
            separator = "&" if "?" in url else "?"
            fetch_url = f"{url}{separator}t={int(time.time())}"
        else:
            fetch_url = url

        try:
            with business_query_log_scope():
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        fetch_url, follow_redirects=True,
                    )
                    if response.status_code == 200:
                        try:
                            config = yaml.safe_load(response.text)
                            if isinstance(config, dict) and all(
                                isinstance(key, str) for key in config
                            ):
                                return config
                        except yaml.YAMLError as error:
                            log_plugin_failure("parse_plugin_source", error)
        except Exception as e:
            log_plugin_failure("fetch_plugin_source", e)
        return None

    def _resolve_relative_urls(
        self, config: Dict[str, Any], base_url: str
    ) -> Dict[str, Any]:
        (
            "Convert relative paths in configuration to absolute URLs using ba"
            "se_url."
        )
        if "source" in config:
            source = config["source"]
            if not isinstance(source, dict):
                raise ValueError("Invalid plugin source configuration")
            # Handle binary source
            binary = source.get("binary")
            if binary is not None:
                if not isinstance(binary, dict):
                    raise ValueError("Invalid plugin binary configuration")
                if "url" in binary:
                    url = binary["url"]
                    if not isinstance(url, str):
                        raise ValueError("Plugin binary URL must be a string")
                    if not url.startswith(("http://", "https://")):
                        binary["url"] = urljoin(base_url, url)

            # Handle github source path (if it were relative to the yml, but
            # usually it's repo-based)
            # However, if it's a relative path in a repo, it might be useful.
            # For now, we mainly focus on binary URLs which are most likely to
            # be relative.

        return config
