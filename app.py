import os
import io
import threading
import time
from flask import Flask, request, send_file, render_template_string, jsonify, Response
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

data_lock = threading.Lock()
latest_frame = None
last_updated = 0

AI_MODEL_URL = os.getenv('AI_MODEL_URL')

BROADCASTER_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HD Camera Broadcaster</title>
    <style>
        body { font-family: sans-serif; text-align: center; background: #1a1a1a; color: #fff; margin: 0; padding: 10px; }
        .container { max-width: 800px; margin: 0 auto; }
        video { width: 100%; max-height: 70vh; background: #000; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }
        #status { margin: 15px 0; padding: 10px; background: #333; border-radius: 4px; font-family: monospace; }
        .btn { padding: 12px 24px; background: #e74c3c; border: none; color: white; border-radius: 5px; cursor: pointer; font-size: 16px; transition: background 0.3s; }
        .btn:hover { background: #c0392b; }
        .btn:disabled { background: #555; cursor: not-allowed; }
        .info { font-size: 0.9em; color: #aaa; margin-top: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <h3>🔴 Live Broadcaster</h3>
        <video id="video" autoplay playsinline muted></video>
        <canvas id="canvas" style="display:none;"></canvas>
        
        <div id="status">Waiting for camera permission...</div>
        <button id="startBtn" class="btn" onclick="startStream()">Start Streaming</button>
        <div class="info">Rotate phone to landscape for best results</div>
    </div>

    <script>
        const video = document.getElementById('video');
        const canvas = document.getElementById('canvas');
        const context = canvas.getContext('2d');
        const status = document.getElementById('status');
        const startBtn = document.getElementById('startBtn');
        let streaming = false;

        async function startStream() {
            try {
                startBtn.disabled = true;
                startBtn.innerText = "Initializing...";
                
                // Request best possible resolution
                const stream = await navigator.mediaDevices.getUserMedia({ 
                    video: { 
                        facingMode: "environment", 
                        width: { ideal: 1920 }, 
                        height: { ideal: 1080 }
                    } 
                });
                video.srcObject = stream;
                
                video.onloadedmetadata = () => {
                    streaming = true;
                    status.innerText = `Camera Ready: ${video.videoWidth}x${video.videoHeight}`;
                    startBtn.innerText = "Streaming Active";
                    startBtn.style.background = "#27ae60";
                    // Start the upload loop
                    uploadLoop();
                };
            } catch (err) {
                status.innerText = "Error: " + err.message;
                status.style.color = "#e74c3c";
                startBtn.disabled = false;
                startBtn.innerText = "Retry";
            }
        }

        // Recursive function to prevent network congestion
        function uploadLoop() {
            if (!streaming) return;

            processAndUpload().then(() => {
                // Wait 1000ms minus execution time, or run immediately if slow
                setTimeout(uploadLoop, 1000); 
            }).catch(err => {
                console.error(err);
                setTimeout(uploadLoop, 2000); // Wait longer on error
            });
        }

        function processAndUpload() {
            return new Promise((resolve, reject) => {
                const vW = video.videoWidth;
                const vH = video.videoHeight;

                if (vW === 0 || vH === 0) return resolve();

                // FORCE LANDSCAPE LOGIC
                // If height > width (Portrait), we swap dimensions and rotate
                if (vH > vW) {
                    canvas.width = vH;
                    canvas.height = vW;
                    context.save();
                    context.translate(vH, 0);
                    context.rotate(90 * Math.PI / 180);
                    context.drawImage(video, 0, 0, vW, vH);
                    context.restore();
                } else {
                    canvas.width = vW;
                    canvas.height = vH;
                    context.drawImage(video, 0, 0, vW, vH);
                }
                
                // Convert to Blob and Upload
                canvas.toBlob(blob => {
                    if (!blob) return resolve();
                    
                    const formData = new FormData();
                    formData.append('file', blob, 'frame.jpg');

                    fetch('/upload', { method: 'POST', body: formData })
                        .then(res => {
                            if(res.ok) {
                                status.innerText = `🟢 Live | Sent: ${canvas.width}x${canvas.height}`;
                                status.style.color = "#2ecc71";
                            } else {
                                status.innerText = `⚠️ Server Error: ${res.status}`;
                                status.style.color = "#f1c40f";
                            }
                            resolve();
                        })
                        .catch(err => {
                            status.innerText = "⚠️ Connection Lost";
                            status.style.color = "#e74c3c";
                            resolve(); // Resolve anyway to keep loop going
                        });
                }, 'image/jpeg', 0.7); // 0.7 Quality to save bandwidth
            });
        }
    </script>
</body>
</html>
"""

VIEWER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Live View</title>
    <meta http-equiv="refresh" content="2">
    <style>
        body { background: #222; color: #fff; text-align: center; font-family: sans-serif; }
        img { max-width: 100%; border: 2px solid #555; height: auto; }
    </style>
</head>
<body>
    <h1>Remote View</h1>
    <img src="/latest.jpg" id="liveImage" onload="updateImage()">
    <p>Auto-refreshing...</p>
    
    <script>
        // Smoother JS refresh instead of meta tag
        function updateImage() {
            setTimeout(() => {
                const img = document.getElementById('liveImage');
                img.src = '/latest.jpg?t=' + new Date().getTime();
            }, 1000);
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    """The Broadcaster Page (Camera)"""
    return render_template_string(BROADCASTER_HTML)

@app.route('/view')
def viewer():
    """A separate page to watch the stream"""
    return render_template_string(VIEWER_HTML)

@app.route('/upload', methods=['POST'])
def upload():
    global latest_frame, last_updated
    if 'file' not in request.files:
        return "No file", 400
    
    file = request.files['file']
    
    in_memory_file = io.BytesIO()
    file.save(in_memory_file)
    in_memory_file.seek(0)
    file_bytes = in_memory_file.getvalue()

    with data_lock:
        latest_frame = file_bytes
        last_updated = time.time()
        
    return "OK", 200

@app.route('/latest.jpg')
def get_latest():
    global latest_frame
    
    with data_lock:
        current_data = latest_frame

    if current_data is None:
        return "No stream available", 404
    
    return send_file(
        io.BytesIO(current_data),
        mimetype='image/jpeg',
        as_attachment=False,
        download_name='latest.jpg'
    )

@app.route('/trigger_analysis', methods=['GET', 'POST']) # Change this line
def trigger_analysis():
    global latest_frame
    
    print("ESP32 Trigger Received!") 

    with data_lock:
        current_data = latest_frame

    if current_data is None:
        return jsonify({"status": "error", "message": "No frame available"}), 404

    if time.time() - last_updated > 10:
        return jsonify({"status": "warning", "message": "Frame is stale (stream may be offline)"}), 404

    if not AI_MODEL_URL or 'mock' in AI_MODEL_URL:
         return jsonify({"status": "mock_success", "message": "AI URL not configured, returning mock data"}), 200

    try:
        response = requests.post(
            AI_MODEL_URL, 
            data=current_data, 
            headers={'Content-Type': 'image/jpeg'},
            timeout=10
        )

        try:
            ai_data = response.json()
        except:
            ai_data = {"raw_response": response.text}

        return jsonify({
            "status": "success", 
            "ai_response": ai_data
        }), 200
        
    except requests.exceptions.Timeout:
        return jsonify({"status": "error", "message": "AI Service Timed Out"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/mock_ai', methods=['POST'])
def mock_ai():
    return jsonify({"prediction": "person", "confidence": 0.98})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)