import os
import io
import threading
import time
import json
import requests
from flask import Flask, request, send_file, render_template_string, jsonify
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

load_dotenv()

AI_MODEL_URL = os.getenv("AI_MODEL_URL")

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT"))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")

MQTT_TRIGGER_TOPIC = "/esp32/ai/trigger"
MQTT_ACK_TOPIC = "/esp32/ai/ack"

app = Flask(__name__)

data_lock = threading.Lock()
latest_frame = None
last_updated = 0

BROADCASTER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Camera Broadcaster</title>
<style>
body{background:#111;color:#fff;font-family:sans-serif;text-align:center}
video{width:100%;max-width:900px}
button{padding:12px 24px;font-size:16px}
</style>
</head>
<body>
<h2>📷 Live Camera</h2>
<video id="video" autoplay muted playsinline></video>
<canvas id="canvas" style="display:none;"></canvas>
<p id="status">Idle</p>
<button onclick="start()">Start</button>

<script>
const video=document.getElementById("video");
const canvas=document.getElementById("canvas");
const ctx=canvas.getContext("2d");
const status=document.getElementById("status");

async function start(){
  const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:"environment"}});
  video.srcObject=stream;
  video.onloadedmetadata=()=>loop();
}

function loop(){
  canvas.width=video.videoWidth;
  canvas.height=video.videoHeight;
  ctx.drawImage(video,0,0);
  canvas.toBlob(b=>{
    const fd=new FormData();
    fd.append("file",b,"frame.jpg");
    fetch("/upload",{method:"POST",body:fd});
  },"image/jpeg",0.7);
  status.innerText="Streaming...";
  setTimeout(loop,1000);
}
</script>
</body>
</html>"""

VIEWER_HTML = """<!DOCTYPE html>
<html>
<body style="background:#111;color:#fff;text-align:center">
<h2>Live View</h2>
<img src="/latest.jpg" style="max-width:95%">
<script>
setInterval(()=>{
 document.querySelector("img").src="/latest.jpg?t="+Date.now()
},1000);
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(BROADCASTER_HTML)

@app.route("/view")
def view():
    return render_template_string(VIEWER_HTML)

@app.route("/upload", methods=["POST"])
def upload():
    global latest_frame, last_updated

    if "file" not in request.files:
        return "No file", 400

    buf = io.BytesIO()
    request.files["file"].save(buf)

    with data_lock:
        latest_frame = buf.getvalue()
        last_updated = time.time()

    return "OK", 200

@app.route("/latest.jpg")
def latest():
    with data_lock:
        frame = latest_frame

    if frame is None:
        return "No frame", 404

    return send_file(io.BytesIO(frame), mimetype="image/jpeg")


@app.route("/trigger_analysis", methods=["POST"])
def trigger_analysis():
    return jsonify(run_ai("manual")), 200

def run_ai(source):
    global latest_frame, last_updated

    with data_lock:
        frame = latest_frame
        ts = last_updated

    if frame is None:
        return {"status": "error", "message": "No frame"}

    if time.time() - ts > 10:
        return {"status": "warning", "message": "Frame stale"}

    try:
        r = requests.post(
            AI_MODEL_URL,
            data=frame,
            headers={"Content-Type": "image/jpeg"},
            timeout=20
        )
        try:
            data = r.json()
        except:
            data = {"raw": r.text}

        return {"status": "success", "source": source, "ai": data}

    except Exception as e:
        return {"status": "error", "message": str(e)}

def on_mqtt_connect(client, userdata, flags, rc):
    print("[MQTT] Connected", rc)
    client.subscribe(MQTT_TRIGGER_TOPIC)

def on_mqtt_message(client, userdata, msg):
    print("[MQTT] Trigger received:", msg.payload.decode())

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

if __name__ == "__main__":
    threading.Thread(target=mqtt_worker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)