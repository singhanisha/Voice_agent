import os
import httpx
import logging
import asyncio

logger = logging.getLogger(__name__)

MOCK_PROPERTIES = [
    {
        "title": "2BHK in Wakad",
        "price": "₹75 Lakhs",
        "location": "Wakad",
        "map_link": "https://maps.google.com/?q=Wakad",
        "brochure": "https://example.com/brochure_wakad.pdf"
    },
    {
        "title": "2BHK in Hinjewadi",
        "price": "₹72 Lakhs",
        "location": "Hinjewadi",
        "map_link": "https://maps.google.com/?q=Hinjewadi"
    }
]

def format_property_message(customer_data: dict, property_list: list) -> str:
    """Format the property details deterministically."""
    name = customer_data.get("name", "there")
    message = f"Hi {name},\n\nAs discussed on our call, here are properties matching your requirement:\n\n"
    
    for idx, prop in enumerate(property_list, 1):
        message += f"{idx}. {prop.get('title', 'Property')}\n"
        message += f"Price: {prop.get('price', 'N/A')}\n"
        message += f"Location: {prop.get('location', 'N/A')}\n"
        if prop.get("map_link"):
            message += f"Map: {prop.get('map_link')}\n"
        if prop.get("brochure"):
            message += f"Brochure: {prop.get('brochure')}\n"
        message += "\n"
        
    message += "Let me know if you'd like to schedule a site visit."
    return message

async def send_whatsapp_message(phone_number: str, message: str) -> None:
    """Asynchronously send a WhatsApp message using the configured provider API."""
    enabled = os.getenv("WHATSAPP_ENABLED", "true").lower() == "true"
    if not enabled:
        logger.info("WhatsApp integration is disabled (WHATSAPP_ENABLED != true).")
        return
        
    api_url = os.getenv("WHATSAPP_API_URL")
    token = os.getenv("WHATSAPP_TOKEN")
    
    if not api_url or not token:
        logger.warning("WhatsApp API credentials missing. Skipping message send.")
        return
        
    timeout_seconds = int(os.getenv("WHATSAPP_TIMEOUT_SECONDS", "5"))
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "to": phone_number,
        "type": "text",
        "text": {
            "body": message
        }
    }
    
    # Retry once on transient failure
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(api_url, headers=headers, json=payload)
                response.raise_for_status()
                logger.info(f"WhatsApp message successfully sent to {phone_number}.")
                return  # Success, exit retry loop
                
        except httpx.HTTPStatusError as e:
            logger.warning(f"WhatsApp API returned HTTP error {e.response.status_code} on attempt {attempt + 1}")
            if e.response.status_code < 500:
                # Don't retry on client errors (4xx)
                break
        except httpx.RequestError as e:
            logger.warning(f"WhatsApp API request failed: {e} on attempt {attempt + 1}")
        except Exception as e:
            logger.warning(f"Unexpected error sending WhatsApp message: {e} on attempt {attempt + 1}")
            
        if attempt == 0:
            await asyncio.sleep(1) # wait before retrying

    logger.warning(f"Failed to send WhatsApp message to {phone_number} after 2 attempts.")
