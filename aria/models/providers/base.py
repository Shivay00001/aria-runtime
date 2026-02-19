"""aria/models/providers/base.py — Provider ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod

from aria.models.types import PromptRequest, RawModelResponse


class ModelProviderInterface(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def call(self, request: PromptRequest) -> RawModelResponse: ...

    @abstractmethod
    def estimate_tokens(self, request: PromptRequest) -> int: ...
