import os
import io
import time
import json
import threading
import requests
import logging
from flask import Flask, request, send_file, render_template_string, jsonify
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

load_dotenv()

AI_MODEL_URL = os.getenv("AI_MODEL_URL", "http://localhost:5002/process_image")
PORT = int(os.getenv("PORT", 5001))

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT   = int(os.getenv("MQTT_PORT", 8883))
MQTT_USER   = os.getenv("MQTT_USER")
MQTT_PASS   = os.getenv("MQTT_PASS")

app = Flask(__name__)

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

latest_frames = {}
lock = threading.Lock()

BROADCASTER_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ParkEase Global Camera</title>
<style>
body{background:#111;color:#fff;font-family:sans-serif;text-align:center; padding: 20px;}
video{width:100%;max-width:800px; border: 2px solid #4CAF50; border-radius: 10px;}
input{padding: 10px; font-size: 16px; width: 80%; max-width: 300px; margin-bottom: 15px; border-radius: 5px; text-align: center;}
button{padding:12px 24px;font-size:16px;margin-top:10px; background: #4CAF50; color: white; border: none; border-radius: 5px; cursor: pointer;}
button:disabled {background: #555;}
</style>
</head>
<body>

<h2>📷 ParkEase Camera Portal</h2>
<p>Enter your Parking Lot Prefix to begin broadcasting:</p>
<input type="text" id="lotPrefix" placeholder="e.g. lot_nitd_MiniCampus" required>
<br>
<button id="start">Start Camera</button>

<div id="videoContainer" style="display:none; margin-top: 20px;">
    <video id="video" autoplay muted playsinline></video>
    <p id="status" style="color: #4CAF50; font-weight: bold;">Broadcasting...</p>
</div>
<canvas id="canvas" style="display:none"></canvas>

<script>
const video  = document.getElementById("video");
const canvas = document.getElementById("canvas");
const ctx    = canvas.getContext("2d");
const status = document.getElementById("status");
const startBtn = document.getElementById("start");
const lotInput = document.getElementById("lotPrefix");
const videoContainer = document.getElementById("videoContainer");

let currentPrefix = "";

startBtn.onclick = async () => {
  if (!lotInput.value) {
      alert("Please enter a Lot Prefix!");
      return;
  }
  
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    alert("Camera not supported on this browser.");
    return;
  }

  currentPrefix = lotInput.value.trim();
  lotInput.disabled = true;
  startBtn.style.display = "none";
  videoContainer.style.display = "block";

  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } }
  });

  video.srcObject = stream;

  video.onloadedmetadata = () => {
    status.innerText = `Broadcasting as: ${currentPrefix}`;
    capture();
  };
};

function capture() {
  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  ctx.drawImage(video, 0, 0);

  canvas.toBlob(blob => {
    const fd = new FormData();
    fd.append("image", blob, "frame.jpg");
    fd.append("lotPrefix", currentPrefix); 
    fetch("/upload", { method: "POST", body: fd });
  }, "image/jpeg", 0.9);

  setTimeout(capture, 1000);
}
</script>

</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(BROADCASTER_HTML)

@app.route("/upload", methods=["POST"])
def upload():
    lot_prefix = request.form.get("lotPrefix")
    
    if not lot_prefix or "image" not in request.files:
        return "Missing data", 400

    buf = io.BytesIO()
    request.files["image"].save(buf)

    with lock:
        latest_frames[lot_prefix] = {
            "frame": buf.getvalue(),
            "ts": time.time()
        }

    return "OK", 200

def run_ai(source, lot_prefix):
    with lock:
        lot_data = latest_frames.get(lot_prefix)

    if not lot_data:
        return {"status": "error", "message": f"No camera currently streaming for {lot_prefix}"}

    frame = lot_data["frame"]
    ts = lot_data["ts"]

    if time.time() - ts > 15:
        return {"status": "warning", "message": "Frame too old, camera might be disconnected"}

    try:
        files = {"image": ("frame.jpg", frame, "image/jpeg")}
        data = {"lotPrefix": lot_prefix} 

        r = requests.post(AI_MODEL_URL, files=files, data=data, timeout=30)
        return {"status": "success", "source": source, "ai_response": r.json()}

    except Exception as e:
        print(f"[{lot_prefix} AI] ERROR:", e)
        return {"status": "error", "message": str(e)}

def on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[MQTT] Connected to broker. Listening for AI triggers...")
        client.subscribe("/esp32/ai/trigger/+", qos=1)
    else:
        print(f"[MQTT] Connection failed, rc={rc}")

def on_mqtt_message(client, userdata, msg):
    lot_prefix = msg.topic.split("/")[-1] 
    payload = msg.payload.decode(errors="ignore")
    
    print(f"[Trigger] {lot_prefix} -> {payload}")

    result = run_ai("mqtt", lot_prefix)

    ack_topic = f"/esp32/ai/ack/{lot_prefix}"
    client.publish(ack_topic, json.dumps(result), qos=1)

def mqtt_worker():
    client = mqtt.Client(client_id="global-camera-portal", protocol=mqtt.MQTTv311)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set()
    client.on_connect = on_mqtt_connect
    client.on_message = on_mqtt_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_forever()

if __name__ == "__main__":
    print("[SYSTEM] Global Camera Portal Starting...")
    threading.Thread(target=mqtt_worker, daemon=False).start()
    app.run(host="0.0.0.0", port=PORT)