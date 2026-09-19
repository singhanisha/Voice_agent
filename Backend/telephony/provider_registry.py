"""
Multi-Provider Telephony Registry.

Clients can choose their telephony provider from the dashboard.
This registry abstracts Twilio, VoBiz, Exotel, etc. behind a single interface.

Supported providers (India-optimised):
  - twilio    : Global standard. Best SDK. ~₹1.2/min India outbound.
  - vobiz     : India-first SIP provider. Already stubbed in vobiz.py.
  - exotel    : Popular India CPaaS. Simple API. ~₹0.50/min.
  - knowlarity : Enterprise India CPaaS. Good for large volumes.
  - demo      : Zero-cost simulation (default).

To add a new provider:
  1. Create Backend/telephony/{provider}_handler.py
  2. Implement TelephonyProvider base class
  3. Register it in PROVIDER_REGISTRY below
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger("telephony.registry")


# ── Base Interface ────────────────────────────────────────────────────────────

class TelephonyProvider(ABC):
    """Abstract base for all telephony providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider slug used in dropdown: 'twilio', 'exotel', etc."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown in UI dropdown."""
        ...

    @property
    @abstractmethod
    def region(self) -> str:
        """Primary region: 'IN', 'US', 'Global'"""
        ...

    @property
    def est_cost_per_min(self) -> str:
        return "—"

    @abstractmethod
    async def initiate_call(
        self,
        to_number: str,
        from_number: str,
        call_id: str,
        webhook_base_url: str,
    ) -> dict:
        """
        Initiate an outbound call.
        Returns: { "call_sid": str, "status": str }
        """
        ...

    @abstractmethod
    async def list_available_numbers(
        self,
        country_code: str = "IN",
        area_code: Optional[str] = None,
    ) -> list[dict]:
        """
        Returns available phone numbers to purchase.
        Each: { "phone": str, "region": str, "monthly_cost": str }
        """
        ...

    @abstractmethod
    async def purchase_number(self, phone_number: str) -> dict:
        """
        Purchase a phone number.
        Returns: { "phone": str, "sid": str, "status": str }
        """
        ...

    def is_configured(self) -> bool:
        """Returns True if all required env vars are set."""
        return True


# ── Twilio ────────────────────────────────────────────────────────────────────

class TwilioProvider(TelephonyProvider):
    name = "twilio"
    display_name = "Twilio (Global)"
    region = "Global"
    est_cost_per_min = "~₹1.2/min"

    def is_configured(self) -> bool:
        return bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN"))

    async def initiate_call(self, to_number, from_number, call_id, webhook_base_url) -> dict:
        try:
            from twilio.rest import Client  # type: ignore
            account_sid = os.getenv("TWILIO_ACCOUNT_SID")
            auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
            client = Client(account_sid, auth_token)

            twiml_url = f"{webhook_base_url}/telephony/twiml/{call_id}"
            call = client.calls.create(
                to=to_number,
                from_=from_number,
                url=twiml_url,
                method="POST",
            )
            logger.info("[TWILIO] Call initiated: %s → %s  SID=%s", from_number, to_number, call.sid)
            return {"call_sid": call.sid, "status": call.status}
        except Exception as e:
            logger.error("[TWILIO] Call initiation failed: %s", e)
            return {"call_sid": "", "status": "failed", "error": str(e)}

    async def list_available_numbers(self, country_code="IN", area_code=None) -> list[dict]:
        try:
            from twilio.rest import Client  # type: ignore
            client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
            numbers = client.available_phone_numbers(country_code).local.list(limit=10)
            return [
                {
                    "phone": n.phone_number,
                    "region": n.locality or country_code,
                    "monthly_cost": "~₹1.2/min",
                    "provider": "twilio",
                }
                for n in numbers
            ]
        except Exception as e:
            logger.error("[TWILIO] List numbers failed: %s", e)
            return []

    async def purchase_number(self, phone_number: str) -> dict:
        try:
            from twilio.rest import Client  # type: ignore
            client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
            number = client.incoming_phone_numbers.create(phone_number=phone_number)
            return {"phone": number.phone_number, "sid": number.sid, "status": "active"}
        except Exception as e:
            logger.error("[TWILIO] Purchase failed: %s", e)
            return {"phone": phone_number, "sid": "", "status": "failed", "error": str(e)}


# ── VoBiz ─────────────────────────────────────────────────────────────────────

class VoBizProvider(TelephonyProvider):
    name = "vobiz"
    display_name = "VoBiz (India)"
    region = "IN"
    est_cost_per_min = "~₹0.40/min"

    def is_configured(self) -> bool:
        return bool(os.getenv("VOBIZ_API_KEY"))

    async def initiate_call(self, to_number, from_number, call_id, webhook_base_url) -> dict:
        """
        VoBiz uses SIP + WebSocket streams.
        When integrated, their platform connects to /telephony/vobiz/stream
        which is already stubbed in telephony/vobiz.py.
        """
        logger.info("[VOBIZ] Initiating call %s→%s (call_id=%s)", from_number, to_number, call_id)
        # VoBiz REST API call would go here
        # For now, returns a mock SID so the system proceeds
        return {"call_sid": f"VBZ-{call_id}", "status": "queued"}

    async def list_available_numbers(self, country_code="IN", area_code=None) -> list[dict]:
        # VoBiz API for number availability
        return [
            {"phone": "+91 8041 234567", "region": "Bangalore, IN", "monthly_cost": "~₹0.40/min", "provider": "vobiz"},
            {"phone": "+91 2241 345678", "region": "Mumbai, IN",    "monthly_cost": "~₹0.40/min", "provider": "vobiz"},
            {"phone": "+91 4041 456789", "region": "Chennai, IN",   "monthly_cost": "~₹0.40/min", "provider": "vobiz"},
        ]

    async def purchase_number(self, phone_number: str) -> dict:
        return {"phone": phone_number, "sid": f"VBZ-{phone_number}", "status": "active"}


# ── Exotel ────────────────────────────────────────────────────────────────────

class ExotelProvider(TelephonyProvider):
    name = "exotel"
    display_name = "Exotel (India)"
    region = "IN"
    est_cost_per_min = "~₹0.50/min"

    def is_configured(self) -> bool:
        return bool(os.getenv("EXOTEL_SID") and os.getenv("EXOTEL_TOKEN"))

    async def initiate_call(self, to_number, from_number, call_id, webhook_base_url) -> dict:
        import httpx
        sid   = os.getenv("EXOTEL_SID", "")
        token = os.getenv("EXOTEL_TOKEN", "")
        subdomain = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")
        url = f"https://{sid}:{token}@{subdomain}/v1/Accounts/{sid}/Calls/connect"

        payload = {
            "From": to_number,
            "To":   from_number,
            "CallerId": from_number,
            "Url": f"{webhook_base_url}/telephony/twiml/{call_id}",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, data=payload)
                resp.raise_for_status()
                data = resp.json()
                return {"call_sid": data.get("Call", {}).get("Sid", ""), "status": "queued"}
        except Exception as e:
            logger.error("[EXOTEL] Call initiation failed: %s", e)
            return {"call_sid": "", "status": "failed", "error": str(e)}

    async def list_available_numbers(self, country_code="IN", area_code=None) -> list[dict]:
        return [
            {"phone": "+91 8068 111111", "region": "Bangalore, IN", "monthly_cost": "~₹0.50/min", "provider": "exotel"},
            {"phone": "+91 2268 222222", "region": "Mumbai, IN",    "monthly_cost": "~₹0.50/min", "provider": "exotel"},
            {"phone": "+91 1168 333333", "region": "Delhi, IN",     "monthly_cost": "~₹0.50/min", "provider": "exotel"},
        ]

    async def purchase_number(self, phone_number: str) -> dict:
        return {"phone": phone_number, "sid": f"EXT-{phone_number}", "status": "active"}


# ── Knowlarity ────────────────────────────────────────────────────────────────

class KnowlarityProvider(TelephonyProvider):
    name = "knowlarity"
    display_name = "Knowlarity (India Enterprise)"
    region = "IN"
    est_cost_per_min = "~₹0.60/min"

    def is_configured(self) -> bool:
        return bool(os.getenv("KNOWLARITY_API_KEY") and os.getenv("KNOWLARITY_ACCOUNT_SID"))

    async def initiate_call(self, to_number, from_number, call_id, webhook_base_url) -> dict:
        logger.info("[KNOWLARITY] Call initiation stubs — configure KNOWLARITY_API_KEY")
        return {"call_sid": f"KNW-{call_id}", "status": "queued"}

    async def list_available_numbers(self, country_code="IN", area_code=None) -> list[dict]:
        return [
            {"phone": "+91 9900 111111", "region": "Pan-India", "monthly_cost": "~₹0.60/min", "provider": "knowlarity"},
        ]

    async def purchase_number(self, phone_number: str) -> dict:
        return {"phone": phone_number, "sid": f"KNW-{phone_number}", "status": "active"}


# ── Demo (no-cost) ────────────────────────────────────────────────────────────

class DemoProvider(TelephonyProvider):
    name = "demo"
    display_name = "Demo Mode (Free)"
    region = "Simulation"
    est_cost_per_min = "Free"

    async def initiate_call(self, to_number, from_number, call_id, webhook_base_url) -> dict:
        return {"call_sid": f"DEMO-{call_id}", "status": "simulated"}

    async def list_available_numbers(self, country_code="IN", area_code=None) -> list[dict]:
        return [{"phone": "+91 00000 00000", "region": "Demo", "monthly_cost": "Free", "provider": "demo"}]

    async def purchase_number(self, phone_number: str) -> dict:
        return {"phone": phone_number, "sid": "DEMO", "status": "simulated"}


# ── Registry ──────────────────────────────────────────────────────────────────

PROVIDER_REGISTRY: dict[str, TelephonyProvider] = {
    "twilio":     TwilioProvider(),
    "vobiz":      VoBizProvider(),
    "exotel":     ExotelProvider(),
    "knowlarity": KnowlarityProvider(),
    "demo":       DemoProvider(),
}


def get_provider(name: str) -> TelephonyProvider:
    """Get a provider by slug. Falls back to demo if unknown."""
    provider = PROVIDER_REGISTRY.get(name)
    if not provider:
        logger.warning("Unknown provider '%s' — falling back to demo", name)
        return PROVIDER_REGISTRY["demo"]
    return provider


def list_providers() -> list[dict]:
    """Returns provider metadata for the frontend dropdown."""
    return [
        {
            "slug":           slug,
            "display_name":   p.display_name,
            "region":         p.region,
            "est_cost_per_min": p.est_cost_per_min,
            "configured":     p.is_configured(),
        }
        for slug, p in PROVIDER_REGISTRY.items()
    ]
