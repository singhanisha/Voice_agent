"""Campaign worker-v2 control plane.

This is the live execution metadata/control surface. The existing v1 call
runner still performs the call dispatch for audio/runtime compatibility, while
worker-v2 records durable execution state, pause/resume/cancel intent, retry
limits, and tenant ownership around that live dispatch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional


logger = logging.getLogger("campaigns.worker_v2")


@dataclass(frozen=True)
class CampaignWorkerV2Config:
    mode: str = "live_metadata"
    max_concurrency: int = 1
    max_attempts: int = 1


class CampaignWorkerV2ControlPlane:
    """Create and control campaign execution metadata for worker-v2."""

    def __init__(self, db):
        self.db = db

    async def prepare_execution(
        self,
        *,
        campaign_id: str,
        agent_id: Optional[str],
        telephony_provider: str,
        client_id: Optional[str] = None,
        requested_by: Optional[str] = None,
        config: CampaignWorkerV2Config | None = None,
    ) -> dict:
        cfg = config or CampaignWorkerV2Config()
        execution = await self.db.create_campaign_execution(
            campaign_id,
            agent_id=agent_id,
            telephony_provider=telephony_provider,
            client_id=client_id,
            mode=cfg.mode,
            max_concurrency=cfg.max_concurrency,
            max_attempts=cfg.max_attempts,
            requested_by=requested_by,
        )
        logger.info(
            "[CAMPAIGN V2] prepared execution=%s campaign=%s mode=%s",
            execution.get("id"),
            campaign_id,
            cfg.mode,
        )
        return execution

    async def pause(self, execution_id: str, reason: str = "") -> Optional[dict]:
        return await self.db.set_campaign_execution_status(
            execution_id,
            "paused",
            event_type="execution_paused",
            payload={"reason": reason},
        )

    async def resume(self, execution_id: str, reason: str = "") -> Optional[dict]:
        return await self.db.set_campaign_execution_status(
            execution_id,
            "planned",
            event_type="execution_resumed",
            payload={"reason": reason},
        )

    async def cancel(self, execution_id: str, reason: str = "") -> Optional[dict]:
        return await self.db.set_campaign_execution_status(
            execution_id,
            "cancelled",
            event_type="execution_cancelled",
            payload={"reason": reason},
        )
