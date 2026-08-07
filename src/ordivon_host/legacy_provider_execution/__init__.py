"""Legacy Host-local Provider execution compatibility.

Current Ordivon Host does not execute model Providers. New cognition execution belongs
outside Host (normally Ordivon Harness or another caller-owned executor). These exports
remain only so retained pre-H2 experiments can be reproduced until compatibility cleanup.
"""

from .model_gateway import (
    CodexCliModelAdapter,
    CodexCliModelGateway,
    HermesCliModelAdapter,
    HermesCliModelGateway,
    ModelAdapter,
    ModelAdapterError,
    ModelGateway,
    ScriptedPreferenceAdapter as LegacyScriptedPreferenceAdapter,
)
from .proposal_adapter import CodexCliProposalAdapter, ProposalAdapterError

__all__ = [
    "CodexCliModelAdapter",
    "CodexCliModelGateway",
    "CodexCliProposalAdapter",
    "HermesCliModelAdapter",
    "HermesCliModelGateway",
    "LegacyScriptedPreferenceAdapter",
    "ModelAdapter",
    "ModelAdapterError",
    "ModelGateway",
    "ProposalAdapterError",
]
