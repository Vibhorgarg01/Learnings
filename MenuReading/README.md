# WhatsApp Menu Ingredient Chatbot - Setup Guide

This bot lets a user send a photo of a restaurant menu, then ask about any
dish on it to get a list of ingredients.

## How it works

1. User sends a **menu photo** on WhatsApp → bot caches it and asks for a dish name
2. User sends a **dish name** as text → bot asks Claude to read the cached
   menu image and extract ingredients for that dish
3. Bot replies with the ingredients

## 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Set up your API keys

```bash
cp .env.example .env
```

Then open `.env` and fill in:
- `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` - from your Twilio Console
- `ANTHROPIC_API_KEY` - from your Anthropic Console

## 3. Set up Twilio WhatsApp Sandbox (free, takes ~5 minutes)

1. Sign up at https://www.twilio.com/try-twilio
2. In the Twilio Console, go to **Messaging → Try it out → Send a WhatsApp message**
3. Follow the instructions to join the sandbox (you send a code like
   "join xxxx-xxxx" from your own WhatsApp to the Twilio sandbox number)
4. Note the sandbox number - that's what you'll message to test your bot

## 4. Run the app locally

```bash
python app.py
```

This starts a Flask server on `http://localhost:5000`.

## 5. Expose it to the internet with ngrok

Twilio needs a public URL to send incoming messages to, so install ngrok
(https://ngrok.com/download) and run:

```bash
ngrok http 5000
```

Copy the `https://xxxx.ngrok-free.app` URL it gives you.

## 6. Point Twilio at your webhook

Back in the Twilio Console (same WhatsApp Sandbox settings page), set:

```
When a message comes in: https://xxxx.ngrok-free.app/whatsapp
Method: POST
```

Save.

## 7. Test it

From your phone, message your Twilio sandbox number:
1. Send a photo of a menu
2. Then send a dish name, e.g. "Chicken Tikka Masala"
3. You should get a reply with the ingredients

## Known limitations (things to improve next)

- **Menu cache is in-memory** - it resets if the server restarts, and won't
  work across multiple server instances. Fine for testing; swap for Redis
  or a database (e.g. SQLite/Postgres) before going to production.
- **One menu per user at a time** - if a user sends a new photo, it
  overwrites their previous cached menu.
- **No conversation state machine** - right now it just checks "was there an
  image?" vs "was there text?". For a more robust bot you may want a proper
  state machine (e.g. using a library or a simple enum stored per-user:
  WAITING_FOR_MENU vs WAITING_FOR_DISH).
- **Twilio sandbox is for testing only** - to go live with real users
  you'll need to apply for WhatsApp Business API access through Twilio,
  which involves Meta's business verification process.
- **No rate limiting / abuse protection** - anyone can message your
  sandbox number and consume your Anthropic API credits. Add basic
  rate limiting before sharing this widely.
