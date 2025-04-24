import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5000))

# Load system prompt
dir_path = os.path.dirname(__file__)
with open(os.path.join(dir_path, "prompt.txt"), "r", encoding="utf-8") as f:
    SYSTEM_MESSAGE = f.read().strip()

VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False

app = FastAPI()
twilio_client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_AUTH"))


if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    response = VoiceResponse()
    response.say(
        "Please wait while we connect your call to the AI voice assistant."
    )
    response.pause(length=1)
    response.say("OK, you can start talking!")
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.post("/missed-call")
async def missed_call(request: Request):
    form = await request.form()
    from_number = form["From"]
    status      = form.get("DialCallStatus") or form.get("CallStatus")
    print(f"🏷️  missed_call webhook hit — From={from_number}, DialCallStatus={status!r} (type={type(status)})")
    if status in ("busy", "no-answer", "failed"):
        try:
            print("✅  Condition met, sending SMS…")
            twilio_client.messages.create(
                body=f"Hey! Sorry we missed your call. How can we help today?",
                from_=os.getenv("TWILIO_NUMBER"),
                to=from_number
            )
        except Exception as e:
            print("❌ Failed to send SMS:", e)
    return Response(status_code=204)

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    await websocket.accept()

    WS_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17"
    async with websockets.connect(
        WS_URL,
        additional_headers=[
            ("Authorization", f"Bearer {OPENAI_API_KEY}"),
            ("OpenAI-Beta", "realtime=v1")
        ]
    ) as openai_ws:
        # Initialize session
        await initialize_session(openai_ws)

        # Connection state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None

        async def receive_from_twilio():
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get('event')
                    if event == 'start':
                        stream_sid = data['start']['streamSid']
                        latest_media_timestamp = 0
                    elif event == 'media':
                        # forward raw audio to OpenAI
                        latest_media_timestamp = int(data['media']['timestamp'])
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }))
                    elif event == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                # close OpenAI socket if Twilio disconnects
                if not openai_ws.closed:
                    await openai_ws.close()

        async def send_to_twilio():
            nonlocal last_assistant_item, response_start_timestamp_twilio
            try:
                async for raw in openai_ws:
                    evt = json.loads(raw)
                    etype = evt.get('type')
                    print("⏱️  EVENT:", evt.get("type"), evt)

                    if etype == "response.done":
    # dig into the nested structure
                        outputs = evt["response"].get("output", [])
                        if outputs:
                            # grab all transcripts/texts and concatenate
                            pieces = []
                            for item in outputs:
                                for chunk in item.get("content", []):
                                    if "transcript" in chunk:
                                        pieces.append(chunk["transcript"])
                                    elif "text" in chunk:
                                        pieces.append(chunk["text"])
                            text = "".join(pieces).strip()
                            print("📝  FINAL TEXT:", repr(text))

                            if text == "<<HANGUP>>" or text.endswith("<<HANGUP>>"):
                                print("📴  HANGUP signal received, closing WS")
                                await websocket.close()
                                return
                            
                    if etype in LOG_EVENT_TYPES:
                        if SHOW_TIMING_MATH:
                            print(f"Event: {etype}", evt)
                    if etype == 'response.audio.delta' and 'delta' in evt:
                        # stream audio back to Twilio
                        payload = evt["delta"]
                        await websocket.send_json({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": payload}
                        })
                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                        if evt.get('item_id'):
                            last_assistant_item = evt['item_id']
                        # mark chunk boundary
                        await send_mark(websocket, stream_sid)
                    # handle interruption
                    if etype == 'input_audio_buffer.speech_started' and last_assistant_item:
                        elapsed = latest_media_timestamp - response_start_timestamp_twilio
                        await openai_ws.send(json.dumps({
                            "type": "conversation.item.truncate",
                            "item_id": last_assistant_item,
                            "content_index": 0,
                            "audio_end_ms": elapsed
                        }))
                        await websocket.send_json({
                            "event": "clear",
                            "streamSid": stream_sid
                        })
            except Exception as e:
                print("Error in send_to_twilio:", e)

        async def send_mark(connection, stream_sid):
            mark_event = {"event": "mark", "streamSid": stream_sid, "mark": {"name": "responsePart"}}
            await connection.send_json(mark_event)
            mark_queue.append('responsePart')

        # Run both tasks concurrently
        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def initialize_session(openai_ws):
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
