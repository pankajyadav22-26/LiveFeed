import os
import io
import time
import json
import threading
import requests
import logging
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

# --- SYSTEM CONFIGURATION ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Suppress noisy Flask logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

AI_MODEL_URL = os.getenv("AI_MODEL_URL", "http://localhost:5002/process_image")
PORT = int(os.getenv("PORT", 5001))

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT   = int(os.getenv("MQTT_PORT", 8883))
MQTT_USER   = os.getenv("MQTT_USER")
MQTT_PASS   = os.getenv("MQTT_PASS")

app = Flask(__name__)

# --- STATE VARIABLES ---
latest_frames = {}
pending_triggers = {}
lock = threading.Lock()
mqtt_client_global = None

# --- FRONTEND UI ---
BROADCASTER_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ParkEase Global Camera</title>
<style>
body{background:#111;color:#fff;font-family:sans-serif;text-align:center; padding: 20px;}
video{width:100%;max-width:800px; border: 2px solid #555; border-radius: 10px; transition: border 0.3s;}
input{padding: 10px; font-size: 16px; width: 80%; max-width: 300px; margin-bottom: 15px; border-radius: 5px; text-align: center;}
button{padding:12px 24px;font-size:16px;margin-top:10px; background: #4CAF50; color: white; border: none; border-radius: 5px; cursor: pointer;}
.status-badge { margin-top: 15px; padding: 10px; border-radius: 8px; font-weight: bold; display: none; }
.idle { background: #333; color: #aaa; }
.active { background: #E8F5E9; color: #2E7D32; border: 2px solid #4CAF50; }
</style>
</head>
<body>

<h2>📷 ParkEase Camera Node</h2>
<p>Enter your Parking Lot Prefix to begin broadcasting:</p>
<input type="text" id="lotPrefix" placeholder="e.g. lot_nitd_MiniCampus" required>
<br>
<button id="start">Start Camera</button>

<div id="videoContainer" style="display:none; margin-top: 20px;">
    <video id="video" autoplay muted playsinline></video>
    <div id="statusBadge" class="status-badge idle">🔋 Camera on Standby (Battery Saver)</div>
</div>
<canvas id="canvas" style="display:none"></canvas>

<script>
const video  = document.getElementById("video");
const canvas = document.getElementById("canvas");
const ctx    = canvas.getContext("2d");
const startBtn = document.getElementById("start");
const lotInput = document.getElementById("lotPrefix");
const videoContainer = document.getElementById("videoContainer");
const statusBadge = document.getElementById("statusBadge");

let currentPrefix = "";

startBtn.onclick = async () => {
  if (!lotInput.value) { alert("Please enter a Lot Prefix!"); return; }
  
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    alert("Camera not supported on this browser."); return;
  }

  currentPrefix = lotInput.value.trim();
  lotInput.disabled = true;
  startBtn.style.display = "none";
  videoContainer.style.display = "block";
  statusBadge.style.display = "inline-block";

  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } }
  });

  video.srcObject = stream;

  video.onloadedmetadata = () => {
    pollForTrigger();
    setInterval(() => { captureFrame("periodic"); }, 60000); 
  };
};

async function pollForTrigger() {
    try {
        let res = await fetch(`/check_trigger/${currentPrefix}`);
        if (res.ok) {
            let data = await res.json();
            if (data.capture === true) {
                captureFrame(data.source);
            }
        }
    } catch(e) { console.error("Polling error:", e); }
    
    setTimeout(pollForTrigger, 300);
}

function captureFrame(sourceTrigger) {
  statusBadge.className = "status-badge active";
  statusBadge.innerText = `📸 Capturing (${sourceTrigger})...`;
  video.style.borderColor = "#4CAF50";

  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  ctx.drawImage(video, 0, 0);

  canvas.toBlob(blob => {
    const fd = new FormData();
    fd.append("image", blob, "frame.jpg");
    fd.append("lotPrefix", currentPrefix); 
    fd.append("source", sourceTrigger); 
    
    fetch("/upload", { method: "POST", body: fd }).finally(() => {
        setTimeout(() => {
            statusBadge.className = "status-badge idle";
            statusBadge.innerText = "🔋 Camera on Standby";
            video.style.borderColor = "#555";
        }, 1000);
    });
  }, "image/jpeg", 0.8);
}
</script>
</body>
</html>
"""

# --- ROUTES ---

@app.route("/")
def index():
    return render_template_string(BROADCASTER_HTML)

@app.route("/check_trigger/<lot_prefix>")
def check_trigger(lot_prefix):
    with lock:
        source = pending_triggers.pop(lot_prefix, None)
        if source:
            return jsonify({"capture": True, "source": source})
    return jsonify({"capture": False})

@app.route("/upload", methods=["POST"])
def upload():
    lot_prefix = request.form.get("lotPrefix")
    source = request.form.get("source", "periodic")
    
    if not lot_prefix or "image" not in request.files:
        return "Missing data", 400

    buf = io.BytesIO()
    request.files["image"].save(buf)
    frame_bytes = buf.getvalue()

    with lock:
        latest_frames[lot_prefix] = {
            "frame": frame_bytes,
            "ts": time.time()
        }

    # Trigger AI processing for high-priority events
    if source in ["mqtt_entrance", "mqtt_exit", "siren"]:
        threading.Thread(target=process_and_ack, args=(lot_prefix, frame_bytes, source), daemon=True).start()

    return "OK", 200

# --- CORE LOGIC ---

def process_and_ack(lot_prefix, frame_bytes, source):
    """Sends frame to AI Engine and routes the response via MQTT."""
    try:
        logger.info(f"Analyzing on-demand frame for {lot_prefix} (Trigger: {source})")
        files = {"image": ("frame.jpg", frame_bytes, "image/jpeg")}
        data = {"lotPrefix": lot_prefix, "source": source} 

        r = requests.post(AI_MODEL_URL, files=files, data=data, timeout=30)
        r.raise_for_status()
        ai_resp = r.json()
        
        result = {"status": "success", "source": source, "ai_response": ai_resp}
        
        # 1. Handle Critical Emergency Preemption
        if ai_resp.get("emergency") == True:
            logger.warning(f"🚨 EMERGENCY VEHICLE DETECTED AT {lot_prefix}!")
            logger.warning("Bypassing Cloud. Sending local Open Command to gate...")
            
            gate_topic = f"/esp32/gate/emergency/lot/{lot_prefix}"
            emergency_payload = json.dumps({
                "command": "open", 
                "reservationId": "EMERGENCY_PREEMPTION"
            })
            
            if mqtt_client_global and mqtt_client_global.is_connected():
                mqtt_client_global.publish(gate_topic, emergency_payload, qos=1)

        # 2. Send Standard Acknowledgment
        ack_topic = f"/esp32/ai/ack/{lot_prefix}"
        if mqtt_client_global and mqtt_client_global.is_connected():
            mqtt_client_global.publish(ack_topic, json.dumps(result), qos=1)
            logger.info(f"Sent AI acknowledgment to {ack_topic}")

    except Exception as e:
        logger.error(f"[{lot_prefix} AI Error]: {e}")
        error_result = {"status": "error", "message": str(e)}
        if mqtt_client_global and mqtt_client_global.is_connected():
            mqtt_client_global.publish(f"/esp32/ai/ack/{lot_prefix}", json.dumps(error_result), qos=1)

# --- MQTT SETUP ---

def on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to MQTT broker. Listening for AI triggers...")
        client.subscribe("/esp32/ai/trigger/+", qos=1)
    else:
        logger.error(f"MQTT Connection failed. Return code: {rc}")

def on_mqtt_message(client, userdata, msg):
    lot_prefix = msg.topic.split("/")[-1] 
    payload = msg.payload.decode(errors="ignore")
    logger.info(f"ESP32 requested frame: {lot_prefix} -> {payload}")
    
    try:
        doc = json.loads(payload)
        event_type = doc.get("event", "mqtt_unknown")
    except json.JSONDecodeError:
        event_type = "mqtt_unknown"

    with lock:
        pending_triggers[lot_prefix] = event_type

def start_mqtt():
    global mqtt_client_global
    mqtt_client_global = mqtt.Client(client_id="global-camera-portal", protocol=mqtt.MQTTv311)
    
    if MQTT_USER and MQTT_PASS:
        mqtt_client_global.username_pw_set(MQTT_USER, MQTT_PASS)
        try:
            mqtt_client_global.tls_set()
        except Exception as e:
            logger.warning(f"Could not set TLS: {e}. Proceeding without encryption.")

    mqtt_client_global.on_connect = on_mqtt_connect
    mqtt_client_global.on_message = on_mqtt_message
    
    while True:
        try:
            mqtt_client_global.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            mqtt_client_global.loop_start() 
            break
        except Exception as e:
            logger.error(f"Retrying connection in 5s... ({e})")
            time.sleep(5)

if __name__ == "__main__":
    logger.info("Global Camera Portal Starting in Battery Saver Mode...")
    threading.Thread(target=start_mqtt, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)