import os
import io
import time
import json
import threading
import requests
from flask import Flask, request, send_file, render_template_string, jsonify
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

# ===================== ENV =====================
load_dotenv()

AI_MODEL_URL = os.getenv("AI_MODEL_URL")

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT   = int(os.getenv("MQTT_PORT"))
MQTT_USER   = os.getenv("MQTT_USER")
MQTT_PASS   = os.getenv("MQTT_PASS")

MQTT_TRIGGER_TOPIC = "/esp32/ai/trigger"
MQTT_ACK_TOPIC     = "/esp32/ai/ack"

PORT = int(os.getenv("PORT", 5001))

# ===================== APP =====================
app = Flask(__name__)

latest_frame = None
last_updated = 0
lock = threading.Lock()

# ===================== HTML (Render HTTPS compatible) =====================
BROADCASTER_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Smart Parking Camera</title>
<style>
body{background:#111;color:#fff;font-family:sans-serif;text-align:center}
video{width:100%;max-width:1000px}
button{padding:12px 24px;font-size:16px;margin-top:10px}
</style>
</head>
<body>

<h2>📷 Camera Feed</h2>
<video id="video" autoplay muted playsinline></video>
<canvas id="canvas" style="display:none"></canvas>
<p id="status">Idle</p>
<button id="start">Start Camera</button>

<script>
const video  = document.getElementById("video");
const canvas = document.getElementById("canvas");
const ctx    = canvas.getContext("2d");
const status = document.getElementById("status");

document.getElementById("start").onclick = async () => {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    alert("Camera not supported");
    return;
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: "environment",
      width:  { ideal: 1920 },
      height: { ideal: 1080 }
    }
  });

  video.srcObject = stream;

  video.onloadedmetadata = () => {
    status.innerText =
      `Camera Ready: ${video.videoWidth}x${video.videoHeight}`;
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
    fetch("/upload", { method: "POST", body: fd });
  }, "image/jpeg", 0.9);

  setTimeout(capture, 1000);
}
</script>

</body>
</html>
"""

# ===================== ROUTES =====================
@app.route("/")
def index():
    return render_template_string(BROADCASTER_HTML)

@app.route("/upload", methods=["POST"])
def upload():
    global latest_frame, last_updated

    if "image" not in request.files:
        return "Missing image", 400

    buf = io.BytesIO()
    request.files["image"].save(buf)

    with lock:
        latest_frame = buf.getvalue()
        last_updated = time.time()

    return "OK", 200

@app.route("/latest.jpg")
def latest():
    with lock:
        if latest_frame is None:
            return "No frame", 404
        return send_file(io.BytesIO(latest_frame), mimetype="image/jpeg")

@app.route("/trigger_analysis", methods=["POST"])
def trigger_manual():
    return jsonify(run_ai("manual")), 200

# ===================== AI CORE =====================
def run_ai(source):
    with lock:
        frame = latest_frame
        ts    = last_updated

    if frame is None:
        return {"status": "error", "message": "No frame available"}

    if time.time() - ts > 10:
        return {"status": "warning", "message": "Frame too old"}

    try:
        files = {
            "image": ("frame.jpg", frame, "image/jpeg")
        }

        r = requests.post(AI_MODEL_URL, files=files, timeout=30)

        print(
            f"[AI] Triggered | source={source} | "
            f"bytes={len(frame)} | http={r.status_code}"
        )

        return {
            "status": "success",
            "source": source,
            "ai_response": r.json()
        }

    except Exception as e:
        print("[AI] ERROR:", e)
        return {"status": "error", "message": str(e)}

# ===================== MQTT =====================
def on_mqtt_connect(client, userdata, flags, rc):
    print("[MQTT] Connected")
    client.subscribe(MQTT_TRIGGER_TOPIC)

def on_mqtt_message(client, userdata, msg):
    payload = msg.payload.decode()
    print(f"[MQTT] Trigger received: {payload}")

    result = run_ai("mqtt")
    client.publish(MQTT_ACK_TOPIC, json.dumps(result))

    print("[MQTT] AI result published")

def mqtt_worker():
    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set()
    client.on_connect = on_mqtt_connect
    client.on_message = on_mqtt_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_forever()

# ===================== START =====================
if __name__ == "__main__":
    threading.Thread(target=mqtt_worker, daemon=True).start()
    print("[SYSTEM] Camera + AI Trigger Server Started")
    app.run(host="0.0.0.0", port=PORT)