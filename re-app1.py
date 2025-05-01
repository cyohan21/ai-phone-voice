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
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")

twilio_client = Client(TWILIO_SID, TWILIO_AUTH)

# Temp in-memory store for CallSid → From number
callers = {}

@app.post("/forward-call")
async def forward_call(request: Request):
    form = await request.form()
    from_number = form.get("From")
    call_sid = form.get("CallSid")
    print(f"📞 Incoming call from {from_number}, SID: {call_sid}")

    # Store caller by CallSid
    if call_sid and from_number:
        callers[call_sid] = from_number

    response = VoiceResponse()
    dial = response.dial(
        timeout=15,
        action="/missed-call",
        caller_id=TWILIO_NUMBER,
        record="record-from-answer",
        recording_status_callback="/check-recording",
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
            print("✅ Missed call condition met, sending SMS…")
            twilio_client.messages.create(
                body="Hey! Sorry we missed your call. How can we help?",
                from_=TWILIO_NUMBER,
                to=from_number
            )
        except Exception as e:
            print("❌ Failed to send SMS:", e)
    else:
        print("📲 Call was answered, no action needed.")

    return Response(status_code=204)


@app.post("/check-recording")
async def check_recording(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid")
    duration = int(form.get("RecordingDuration", "0"))
    from_number = callers.get(call_sid)

    print(f"🎙️ Recording SID: {call_sid}, Duration: {duration}s, Caller: {from_number or 'Unknown'}")

    if not from_number:
        print("❌ No caller number found — skipping SMS.")
        return Response(status_code=204)

    if duration < 40:
        try:
            print("⚠️ Short call detected — sending follow-up SMS...")
            twilio_client.messages.create(
                body="Hey! Sorry we missed your call. How can we help?",
                from_=TWILIO_NUMBER,
                to=from_number
            )
        except Exception as e:
            print("❌ Failed to send SMS for short call:", e)

    return Response(status_code=204)