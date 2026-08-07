"""Deprecated compatibility shim for pre-H2 Host-local model execution."""

from ..legacy_provider_execution.model_gateway import (
    CodexCliModelAdapter,
    CodexCliModelGateway,
    HermesCliModelAdapter,
    HermesCliModelGateway,
    ModelAdapter,
    ModelAdapterError,
    ModelGateway,
)

__all__ = [
    "CodexCliModelAdapter",
    "CodexCliModelGateway",
    "HermesCliModelAdapter",
    "HermesCliModelGateway",
    "ModelAdapter",
    "ModelAdapterError",
    "ModelGateway",
]
