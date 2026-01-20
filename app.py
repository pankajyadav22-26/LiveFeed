import os
import io
from flask import Flask, request, send_file, render_template_string
from PIL import Image

app = Flask(__name__)

# Global variable to hold the latest frame
latest_frame = None

# HTML Interface for the Phone (Broadcaster)
BROADCASTER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Phone Camera Broadcaster</title>
    <style>
        body { font-family: sans-serif; text-align: center; background: #222; color: #fff; margin: 0; padding: 20px; }
        video { width: 100%; max-width: 400px; border: 2px solid #555; border-radius: 8px; }
        #status { margin-top: 10px; color: #0f0; font-size: 14px; }
        .btn { padding: 10px 20px; background: #e74c3c; border: none; color: white; border-radius: 5px; cursor: pointer; font-size: 16px; margin-top: 15px; }
    </style>
</head>
<body>
    <h3>🔴 Live Broadcaster</h3>
    <video id="video" autoplay playsinline muted></video>
    <canvas id="canvas" width="320" height="240" style="display:none;"></canvas>
    <div id="status">Waiting for camera...</div>
    <button class="btn" onclick="startStream()">Start Streaming</button>

    <script>
        const video = document.getElementById('video');
        const canvas = document.getElementById('canvas');
        const context = canvas.getContext('2d');
        const status = document.getElementById('status');
        let streaming = false;

        async function startStream() {
            try {
                // Request Camera (Rear camera preferred)
                const stream = await navigator.mediaDevices.getUserMedia({ 
                    video: { facingMode: "environment", width: 320, height: 240 } 
                });
                video.srcObject = stream;
                streaming = true;
                status.innerText = "Camera Active. Sending frames...";
                
                // Start Loop
                setInterval(sendFrame, 500); // 2 FPS (Adjust speed here)
            } catch (err) {
                status.innerText = "Error: " + err;
                status.style.color = "red";
            }
        }

        function sendFrame() {
            if (!streaming) return;
            
            // Draw frame to hidden canvas
            context.drawImage(video, 0, 0, 320, 240);
            
            // Convert to JPEG Blob and Upload
            canvas.toBlob(blob => {
                const formData = new FormData();
                formData.append('file', blob, 'frame.jpg');

                fetch('/upload', { method: 'POST', body: formData })
                    .then(res => {
                        if(res.ok) status.innerText = "🟢 Broadcasting Live...";
                    })
                    .catch(err => status.innerText = "⚠️ Upload Error (Server Sleeping?)");
            }, 'image/jpeg', 0.6);
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(BROADCASTER_HTML)

@app.route('/upload', methods=['POST'])
def upload():
    global latest_frame
    if 'file' not in request.files:
        return "No file", 400
    
    file = request.files['file']
    img_io = io.BytesIO()
    file.save(img_io)
    img_io.seek(0)
    latest_frame = img_io.getvalue()
    return "OK", 200

@app.route('/latest.jpg')
def get_latest():
    global latest_frame
    if latest_frame is None:
        return "No stream yet", 404
    
    return send_file(
        io.BytesIO(latest_frame),
        mimetype='image/jpeg',
        as_attachment=False,
        download_name='latest.jpg'
    )

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)