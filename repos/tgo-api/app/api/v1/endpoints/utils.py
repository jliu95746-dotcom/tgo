"""Utility endpoints for various helper functions."""

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl

from app.core.logging import get_logger
from app.core.security import get_current_active_user
from app.models.staff import Staff
from app.services.website_fetch import (
    fetch_website,
    WebsiteTargetRejected,
    WebsiteResponseRejected,
)
from app.services.website_metadata import extract_metadata as _extract_metadata

logger = get_logger("endpoints.utils")
router = APIRouter()

# Constants

REQUEST_TIMEOUT = 10.0  # seconds
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class WebsiteMetadataRequest(BaseModel):
    """Request schema for extracting website metadata."""

    url: HttpUrl = Field(
        ..., description="The URL of the website to extract metadata from"
    )


class WebsiteMetadataResponse(BaseModel):
    """Response schema for website metadata extraction."""

    url: str = Field(..., description="The original URL")
    title: Optional[str] = Field(
        None, description="The website title from <title> tag"
    )
    description: Optional[str] = Field(
        None,
        description="The website description from meta description or og:description tag",
    )
    favicon: Optional[str] = Field(
        None, description="The favicon URL if found"
    )
    og_image: Optional[str] = Field(
        None, description="Open Graph image URL if found"
    )
    success: bool = Field(
        ..., description="Whether the extraction was successful"
    )
    error: Optional[str] = Field(
        None, description="Error message if extraction failed"
    )


@router.post(
    "/extract-website-metadata",
    response_model=WebsiteMetadataResponse,
    summary="Extract Website Metadata",
    description="""
    Extract metadata (title, description, favicon, og:image) from a given website URL.

    This endpoint fetches the HTML content of the specified URL and extracts:
    - **title**: From the `<title>` tag
    - **description**: From `<meta name="description">` or `<meta property="og:description">`
    - **favicon**: From `<link rel="icon">` or similar
    - **og_image**: From `<meta property="og:image">`
    """,
    responses={
        200: {"description": "Metadata extracted successfully"},
        400: {"description": "Invalid URL format"},
        502: {"description": "Failed to fetch the website"},
        504: {"description": "Request timeout"},
    },
)
async def extract_website_metadata(
    request: WebsiteMetadataRequest,
    current_user: Staff = Depends(get_current_active_user),
) -> WebsiteMetadataResponse:
    """Extract metadata from a website URL."""
    url = str(request.url)
    logger.info(
        "Extracting website metadata",
        extra={"user_id": str(current_user.id)},
    )

    try:
        response = await fetch_website(
            url,
            timeout=REQUEST_TIMEOUT,
            user_agent=DEFAULT_USER_AGENT,
        )
        metadata = _extract_metadata(response.text, str(response.url))
        return WebsiteMetadataResponse(
            url=url, success=True, error=None, **metadata
        )
    except WebsiteTargetRejected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only public HTTP and HTTPS websites can be fetched",
        ) from None
    except WebsiteResponseRejected:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Website response exceeded safe preview limits",
        ) from None
    except httpx.TimeoutException:
        logger.warning("Website metadata extraction timeout")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Request timeout while fetching website",
        ) from None
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Website returned error status",
            extra={"status": e.response.status_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Website returned status {e.response.status_code}",
        ) from None
    except httpx.RequestError as e:
        logger.warning(
            "Failed to fetch website", extra={"error_type": type(e).__name__}
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to connect to website",
        ) from None
    except Exception as e:
        logger.error(
            "Unexpected error extracting metadata",
            extra={"error_type": type(e).__name__},
        )
        return WebsiteMetadataResponse(
            url=url,
            success=False,
            error="Unable to extract website metadata",
            title=None,
            description=None,
            favicon=None,
            og_image=None,
        )
