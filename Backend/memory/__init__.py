"""Tenant-isolated memory scaffolding.

Phase 9 stores reviewable memory items only. It does not build embeddings,
perform shared retrieval, or inject memory into live calls.
"""

from .isolated_store import AgentMemoryService

__all__ = ["AgentMemoryService"]
