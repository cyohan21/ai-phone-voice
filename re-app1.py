import os
from fastapi import FastAPI, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Twilio config
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE")  # The number to forward calls to
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")

twilio_client = Client(TWILIO_SID, TWILIO_AUTH)

@app.post("/forward-call")
async def forward_call(request: Request):
    form = await request.form()
    from_number = form.get("From")
    print(f"📞 Incoming call from {from_number}")

    response = VoiceResponse()

    # Forward call to business number for 20 seconds
    dial = response.dial(
        timeout=20,
        action="/missed-call",  # Twilio will hit this after dial attempt
        caller_id=TWILIO_NUMBER
    )
    dial.number(BUSINESS_PHONE)

    return Response(content=str(response), media_type="application/xml")


@app.post("/missed-call")
async def missed_call(request: Request):
    form = await request.form()
    from_number = form.get("From")
    status = form.get("DialCallStatus") or form.get("CallStatus")
    print(f"🏷️  missed_call webhook hit — From={from_number}, DialCallStatus={status!r}")

    if status in ("busy", "no-answer", "failed", "canceled"):
        try:
            print("✅  Missed call condition met, sending SMS…")
            twilio_client.messages.create(
                body="Hey! Sorry we missed your call. How can we help today?",
                from_=TWILIO_NUMBER,
                to=from_number
            )

            # Optional: Trigger your AI message handler here
            # await handle_ai_message(from_number, "Missed call — user did not connect.")

        except Exception as e:
            print("❌ Failed to send SMS:", e)

    else:
        print("📲 Call was answered, no action needed.")

    return Response(status_code=204)