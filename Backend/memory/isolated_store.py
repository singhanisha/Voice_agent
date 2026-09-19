"""Tenant-safe memory service for future RAG/training workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemorySeed:
    source_type: str
    source_id: str | None
    content: str
    metadata: dict[str, Any]


class AgentMemoryService:
    def __init__(self, db):
        self.db = db

    async def create_collection(
        self,
        *,
        client_id: str,
        agent_id: str,
        source_type: str = "manual",
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        return await self.db.create_agent_memory_collection(
            client_id=client_id,
            agent_id=agent_id,
            source_type=source_type,
            source_id=source_id,
            metadata=metadata or {},
        )

    async def add_item(
        self,
        *,
        collection_id: str,
        client_id: str,
        agent_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        return await self.db.add_agent_memory_item(
            collection_id=collection_id,
            client_id=client_id,
            agent_id=agent_id,
            content=content,
            metadata=metadata or {},
        )

    async def seed_from_agent(self, *, client_id: str, agent: dict) -> dict:
        collection = await self.create_collection(
            client_id=client_id,
            agent_id=agent["id"],
            source_type="agent_profile",
            source_id=agent["id"],
            metadata={
                "agent_name": agent.get("name"),
                "agent_type": agent.get("agent_type"),
                "rag_runtime_enabled": False,
            },
        )
        seeds = self._agent_seeds(agent)
        items = []
        for seed in seeds:
            items.append(await self.add_item(
                collection_id=collection["id"],
                client_id=client_id,
                agent_id=agent["id"],
                content=seed.content,
                metadata=seed.metadata,
            ))
        return {"collection": collection, "items": items}

    async def list_items(self, *, client_id: str, agent_id: str, include_deleted: bool = False) -> list[dict]:
        return await self.db.list_agent_memory_items(
            client_id=client_id,
            agent_id=agent_id,
            include_deleted=include_deleted,
        )

    async def reset(self, *, client_id: str, agent_id: str, reason: str | None = None) -> dict:
        return await self.db.reset_agent_memory(
            client_id=client_id,
            agent_id=agent_id,
            reason=reason,
        )

    def _agent_seeds(self, agent: dict) -> list[MemorySeed]:
        seeds: list[MemorySeed] = []
        script = (agent.get("script") or "").strip()
        if script:
            seeds.append(MemorySeed(
                source_type="agent_script",
                source_id=agent["id"],
                content=script,
                metadata={"source": "agent.script"},
            ))
        fields = agent.get("data_fields") or []
        if fields:
            seeds.append(MemorySeed(
                source_type="agent_slots",
                source_id=agent["id"],
                content=", ".join(str(field) for field in fields),
                metadata={"source": "agent.data_fields"},
            ))
        return seeds
