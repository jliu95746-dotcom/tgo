"""Protocols and registry for replaceable multimodal providers."""

from typing import Protocol

from app.schemas.multimodal import (
    ASROutput,
    ASRRequest,
    AnalysisErrorCategory,
    OCROutput,
    OCRRequest,
    VLMOutput,
    VLMRequest,
)


class ProviderRegistryError(ValueError):
    """Base error for invalid provider registry operations."""


class DuplicateProviderError(ProviderRegistryError):
    """Raised when a capability already has a provider with the same name."""


class ProviderExecutionError(RuntimeError):
    """Provider failure with a safe message and stable classification."""

    def __init__(
        self,
        public_message: str,
        category: AnalysisErrorCategory = (
            AnalysisErrorCategory.PROVIDER_FAILURE
        ),
        retryable: bool = True,
    ) -> None:
        if not public_message.strip():
            raise ValueError("public_message cannot be empty")
        super().__init__(public_message)
        self.public_message = public_message
        self.category = category
        self.retryable = retryable


class ASRProvider(Protocol):
    """Structural interface implemented by speech recognition providers."""

    name: str

    async def transcribe(self, request: ASRRequest) -> ASROutput:
        """Transcribe one validated voice media object."""
        ...


class OCRProvider(Protocol):
    """Structural interface implemented by text extraction providers."""

    name: str

    async def extract_text(self, request: OCRRequest) -> OCROutput:
        """Extract visible text from one validated image."""
        ...


class VLMProvider(Protocol):
    """Structural interface implemented by visual language model providers."""

    name: str

    async def describe(self, request: VLMRequest) -> VLMOutput:
        """Describe one validated image without executing business actions."""
        ...


class MultimodalProviderRegistry:
    """Typed registry, normally populated during application startup."""

    def __init__(self) -> None:
        self._asr_providers: dict[str, ASRProvider] = {}
        self._ocr_providers: dict[str, OCRProvider] = {}
        self._vlm_providers: dict[str, VLMProvider] = {}

    @staticmethod
    def _provider_name(name: str) -> str:
        normalized_name = name.strip()
        if not normalized_name:
            raise ProviderRegistryError("provider name cannot be empty")
        return normalized_name

    def register_asr(self, provider: ASRProvider) -> None:
        """Register one ASR provider by its stable name."""
        provider_name = self._provider_name(provider.name)
        if provider_name in self._asr_providers:
            raise DuplicateProviderError(
                f"ASR provider already registered: {provider_name}"
            )
        self._asr_providers[provider_name] = provider

    def register_ocr(self, provider: OCRProvider) -> None:
        """Register one OCR provider by its stable name."""
        provider_name = self._provider_name(provider.name)
        if provider_name in self._ocr_providers:
            raise DuplicateProviderError(
                f"OCR provider already registered: {provider_name}"
            )
        self._ocr_providers[provider_name] = provider

    def register_vlm(self, provider: VLMProvider) -> None:
        """Register one VLM provider by its stable name."""
        provider_name = self._provider_name(provider.name)
        if provider_name in self._vlm_providers:
            raise DuplicateProviderError(
                f"VLM provider already registered: {provider_name}"
            )
        self._vlm_providers[provider_name] = provider

    def get_asr(self, name: str) -> ASRProvider | None:
        """Return a named ASR provider when configured."""
        return self._asr_providers.get(name)

    def get_ocr(self, name: str) -> OCRProvider | None:
        """Return a named OCR provider when configured."""
        return self._ocr_providers.get(name)

    def get_vlm(self, name: str) -> VLMProvider | None:
        """Return a named VLM provider when configured."""
        return self._vlm_providers.get(name)
