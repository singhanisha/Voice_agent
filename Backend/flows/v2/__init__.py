"""FlowSpec v2 sidecar package.

Flow v2 owns validation, preview, editing, and publishing metadata. Live calls
still use the existing StateManager/audio runtime; publishing converts a
validated FlowSpec into the compatible v1 conversationFlow schema.
"""

from .spec import FlowSpecValidationError, build_flow_spec_from_agent, validate_flow_spec
from .shadow_runner import FlowShadowRunner, ShadowTurnResult
from .preview import build_flow_preview

__all__ = [
    "FlowShadowRunner",
    "FlowSpecValidationError",
    "ShadowTurnResult",
    "build_flow_preview",
    "build_flow_spec_from_agent",
    "validate_flow_spec",
]
