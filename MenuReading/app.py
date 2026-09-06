"""
WhatsApp Menu Ingredient Chatbot - Skeleton Code
--------------------------------------------------
Flow:
1. User sends an image (menu) + text (dish name) via WhatsApp
2. Twilio forwards the incoming message to this Flask webhook
3. We download the image, send it + the dish name to Claude (vision model)
4. Claude returns structured JSON with the ingredients
5. We format a reply and send it back to the user via Twilio

Setup required before running:
- pip install -r requirements.txt
- Create a .env file (see .env.example) with your API keys
- Run: python app.py
- Expose it publicly with: ngrok http 5000
- Set that ngrok URL + "/whatsapp" as your Twilio Sandbox webhook
"""

import os
import base64
import json
import logging

from flask import Flask, request
from dotenv import load_dotenv
import requests
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from google import genai
from google.genai import types as genai_types

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

load_dotenv()  # Loads variables from a .env file into environment variables

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# In-memory cache: { whatsapp_number: base64_image_data }
# This lets a user send the menu photo once, then ask about multiple dishes
# without re-uploading the image each time.
# NOTE: This is per-process memory only - it will reset if the server restarts,
# and won't work if you scale to multiple server instances. Fine for a first
# version; swap for Redis/a database later.
user_menu_cache = {}


# ---------------------------------------------------------------------------
# The enhanced prompt from earlier
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a menu analysis assistant integrated into a WhatsApp chatbot. You will be given:
1. An image of a restaurant menu
2. The name of a specific dish (as typed by a user, which may contain typos, partial names, or different casing)

Your task:
1. Carefully read all text visible in the menu image, including dish names, descriptions, and any ingredient lists provided.
2. Match the user's requested dish name to the closest dish on the menu, even if the input has minor spelling variations, is partially typed, or uses different capitalization.
3. Extract the full list of ingredients for that dish based on:
   - Explicit ingredient lists in the menu description
   - Reasonable inference from the dish name/description if the menu doesn't list ingredients explicitly (clearly marked as inferred, not confirmed)

Output strictly as JSON in this format:
{
  "matched_dish": "<exact dish name found on the menu>",
  "match_confidence": "<high | medium | low>",
  "ingredients": ["<ingredient 1>", "<ingredient 2>", "..."],
  "ingredients_source": "<explicit | inferred>",
  "notes": "<any caveats, e.g. 'menu did not list allergens' or 'dish not found, showing closest match'>",
  "dish_found": <true | false>
}

Rules:
- If the dish is not found on the menu at all, set "dish_found": false, leave "ingredients" as an empty array, and suggest the closest 2-3 dish names from the menu in "notes".
- Never fabricate ingredients not implied by the dish name or menu text - if uncertain, say so in "notes" rather than guessing silently.
- If the menu image is blurry, cropped, or partially unreadable, note this explicitly rather than guessing at missing text.
- Do not include any text outside the JSON object in your response."""

gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def download_twilio_media(media_url: str) -> bytes:
    """Download WhatsApp media using Account SID + Auth Token Basic Auth.

    The MediaUrl0 in the webhook POST belongs to your own account, so
    authenticating with your Account SID + Auth Token works directly.
    allow_redirects=True follows Twilio's redirect to the actual CDN file.
    """
    response = requests.get(
        media_url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        allow_redirects=True,
    )
    response.raise_for_status()
    return response.content


def encode_image_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


def ask_claude_about_dish(image_b64: str, media_type: str, dish_name: str) -> dict:
    """
    Sends the menu image + dish name to Gemini and parses the JSON response.
    Returns a dict. If parsing fails, returns a fallback error dict.
    """
    response = gemini_client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[
            genai_types.Part.from_bytes(
                data=base64.b64decode(image_b64),
                mime_type=media_type,
            ),
            f"The dish the user is asking about is: \"{dish_name}\"",
        ],
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )

    raw_text = response.text.strip()

    # Defensive cleanup in case the model wraps the JSON in code fences
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:].strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude response as JSON: %s", raw_text)
        return {
            "matched_dish": None,
            "match_confidence": "low",
            "ingredients": [],
            "ingredients_source": "inferred",
            "notes": "Sorry, I had trouble reading the menu. Could you try sending a clearer photo?",
            "dish_found": False,
        }


def format_reply(result: dict) -> str:
    """Turns the JSON result into a friendly WhatsApp text reply."""
    if not result.get("dish_found"):
        return (
            f"I couldn't find that dish on the menu. {result.get('notes', '')}"
        ).strip()

    ingredients = result.get("ingredients", [])
    ingredients_list = "\n".join(f"- {item}" for item in ingredients) or "None found"

    source_note = (
        " (inferred, not explicitly listed on the menu)"
        if result.get("ingredients_source") == "inferred"
        else ""
    )

    reply = (
        f"*{result.get('matched_dish')}*\n\n"
        f"Ingredients{source_note}:\n{ingredients_list}"
    )

    if result.get("notes"):
        reply += f"\n\n_Note: {result['notes']}_"

    return reply


# ---------------------------------------------------------------------------
# Webhook route - Twilio hits this whenever a WhatsApp message arrives
# ---------------------------------------------------------------------------

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    from_number = request.values.get("From")
    body_text = (request.values.get("Body") or "").strip()
    num_media = int(request.values.get("NumMedia", 0))

    twiml_response = MessagingResponse()

    logger.info("Incoming: from=%s num_media=%s body=%r", from_number, num_media, body_text)

    try:
        # Case 1: User sent an image (assume it's the menu)
        if num_media > 0:
            media_url = request.values.get("MediaUrl0", "")
            media_type = request.values.get("MediaContentType0", "image/jpeg")

            image_bytes = download_twilio_media(media_url)
            image_b64 = encode_image_base64(image_bytes)

            # Cache it against this user's number so they don't have to
            # resend the photo for every dish they ask about
            user_menu_cache[from_number] = {
                "image_b64": image_b64,
                "media_type": media_type,
            }

            reply = "Got the menu! Now send me the name of the dish you want ingredients for."
            twiml_response.message(reply)
            logger.info("Image cached for %s, sent: %r", from_number, reply)

        # Case 2: User sent text (assume it's a dish name), and we have
        # a cached menu image for them
        elif body_text:
            cached = user_menu_cache.get(from_number)
            logger.info("Text message from %s, cache hit=%s", from_number, cached is not None)

            if not cached:
                twiml_response.message(
                    "Please send me a photo of the menu first, then tell me "
                    "which dish you'd like ingredients for."
                )
            else:
                result = ask_claude_about_dish(
                    image_b64=cached["image_b64"],
                    media_type=cached["media_type"],
                    dish_name=body_text,
                )
                reply = format_reply(result)
                logger.info("Gemini result: %s | reply: %r", result, reply)
                twiml_response.message(reply)

        else:
            twiml_response.message(
                "Send me a photo of a menu, then ask about any dish on it!"
            )

    except Exception as exc:  # noqa: BLE001 - top-level safety net for a webhook
        logger.exception("Error handling WhatsApp message")
        twiml_response.message(
            "Something went wrong on my end. Please try again in a moment."
        )

    twiml_str = str(twiml_response)
    logger.info("TwiML response: %s", twiml_str)
    return twiml_str


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
