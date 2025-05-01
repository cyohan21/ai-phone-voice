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
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE")  # Number to forward calls to
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")

twilio_client = Client(TWILIO_SID, TWILIO_AUTH)


@app.post("/forward-call")
async def forward_call(request: Request):
    form = await request.form()
    from_number = form.get("From")
    print(f"📞 Incoming call from {from_number}")

    response = VoiceResponse()

    # Forward the call and record it
    dial = response.dial(
        timeout=20,
        action="/missed-call",  # Triggered after dial attempt ends
        caller_id=TWILIO_NUMBER,
        record="record-from-answer",
        recording_status_callback="/check-recording",  # For short call detection
        recording_status_callback_event="completed"
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
            # Optional: Trigger your AI assistant here
            # await handle_ai_message(from_number, "Missed call — user did not connect.")
        except Exception as e:
            print("❌ Failed to send SMS:", e)
    else:
        print("📲 Call was answered, no action needed.")

    return Response(status_code=204)


@app.post("/check-recording")
async def check_recording(request: Request):
    form = await request.form()
    from_number = form.get("From")
    duration = int(form.get("RecordingDuration", "0"))
    print(f"🎙️ Recording duration from {from_number}: {duration} seconds")

    # Treat short calls (e.g., <10s) as missed
    if duration < 10:
        try:
            print("⚠️ Short call detected — sending follow-up SMS...")
            twilio_client.messages.create(
                body="Looks like we missed your call or it ended quickly. How can we help?",
                from_=TWILIO_NUMBER,
                to=from_number
            )
            # Optional: Trigger AI again
            # await handle_ai_message(from_number, "Short call — follow-up initiated.")
        except Exception as e:
            print("❌ Failed to send SMS for short call:", e)

    return Response(status_code=204)