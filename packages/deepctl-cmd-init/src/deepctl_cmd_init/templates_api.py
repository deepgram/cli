"""HTTP client for the Deepgram templates API."""

from __future__ import annotations

import httpx

from .models import TemplateDetail, TemplateInfo, TemplateListResponse

BASE_URL = "https://templates.dx.deepgram.com"
DEFAULT_TIMEOUT = 15.0


def list_templates(
    search: str | None = None,
    page: int = 1,
    limit: int = 100,
) -> TemplateListResponse:
    """Fetch templates from the API.

    Args:
        search: Optional search/filter term.
        page: Page number (1-based).
        limit: Results per page.

    Returns:
        Parsed template list response.
    """
    params: dict[str, str | int] = {"page": page, "limit": limit}
    if search:
        params["search"] = search

    resp = httpx.get(
        f"{BASE_URL}/api/templates",
        params=params,
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return TemplateListResponse.model_validate(resp.json())


def get_template(slug: str) -> TemplateDetail:
    """Fetch a single template by slug.

    Args:
        slug: Template name/slug (e.g. "node-transcription").

    Returns:
        Parsed template detail.
    """
    resp = httpx.get(
        f"{BASE_URL}/api/templates/{slug}",
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return TemplateDetail.model_validate(resp.json())


def filter_templates(templates: list[TemplateInfo], search: str) -> list[TemplateInfo]:
    """Client-side filter of templates by search term.

    Matches against name, title, description, language, framework, and category.

    Args:
        templates: List of templates to filter.
        search: Search term (case-insensitive).

    Returns:
        Filtered list.
    """
    term = search.lower()
    results: list[TemplateInfo] = []
    for t in templates:
        fields = [t.name, t.title, t.description, t.language]
        if t.framework:
            fields.append(t.framework)
        if t.category:
            fields.append(t.category)
        if any(term in f.lower() for f in fields):
            results.append(t)
    return results
