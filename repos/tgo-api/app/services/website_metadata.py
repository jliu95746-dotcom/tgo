"""Extract typed presentation data from untrusted website markup."""

from urllib.parse import urljoin, urlsplit
from typing_extensions import TypedDict

from bs4 import BeautifulSoup, Tag
from bs4.element import PageElement


class WebsiteMetadata(TypedDict):
    title: str | None
    description: str | None
    favicon: str | None
    og_image: str | None


def _attribute(tag: PageElement | None, name: str) -> str | None:
    if not isinstance(tag, Tag):
        return None
    value = tag.get(name)
    return value.strip() or None if isinstance(value, str) else None


def _web_link(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    try:
        result = urljoin(base_url, value)
        parsed = urlsplit(result)
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            return result
    except ValueError:
        pass
    return None


def extract_metadata(html: str, base_url: str) -> WebsiteMetadata:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    title = (
        title_tag.get_text(strip=True) or None
        if isinstance(title_tag, Tag)
        else None
    )
    description = _attribute(
        soup.find("meta", attrs={"name": "description"}), "content"
    )
    if description is None:
        description = _attribute(
            soup.find("meta", attrs={"property": "og:description"}), "content"
        )
    favicon = None
    for link in soup.find_all("link"):
        relation = link.get("rel")
        if isinstance(relation, str):
            relations = relation.lower().split()
        elif isinstance(relation, list):
            relations = [part.lower() for part in relation]
        else:
            continue
        if any("icon" in part for part in relations):
            favicon = _web_link(_attribute(link, "href"), base_url)
            if favicon:
                break
    og_image = _web_link(
        _attribute(
            soup.find("meta", attrs={"property": "og:image"}), "content"
        ),
        base_url,
    )
    return {
        "title": title,
        "description": description,
        "favicon": favicon,
        "og_image": og_image,
    }
