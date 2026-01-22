import os
import io
from flask import Flask, request, send_file, render_template_string

app = Flask(__name__)

# Global variable to hold the latest frame
latest_frame = None

# HTML Interface for the Phone (Broadcaster)
BROADCASTER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HD Camera Broadcaster</title>
    <style>
        body { font-family: sans-serif; text-align: center; background: #222; color: #fff; margin: 0; padding: 20px; }
        video { width: 100%; max-width: 640px; border: 2px solid #555; border-radius: 8px; }
        #status { margin-top: 10px; color: #0f0; font-size: 14px; }
        .btn { padding: 10px 20px; background: #e74c3c; border: none; color: white; border-radius: 5px; cursor: pointer; font-size: 16px; margin-top: 15px; }
    </style>
</head>
<body>
    <h3>🔴 Live Broadcaster (HD)</h3>
    <video id="video" autoplay playsinline muted></video>
    <canvas id="canvas" style="display:none;"></canvas>
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
                // --- UPDATE: Request HD Resolution (1920x1080) ---
                // This is required so your AI coordinates (x=1833) fit inside the image.
                const stream = await navigator.mediaDevices.getUserMedia({ 
                    video: { 
                        facingMode: "environment", 
                        width: { ideal: 1920 }, 
                        height: { ideal: 1080 } 
                    } 
                });
                video.srcObject = stream;
                
                // Wait for video to actually load to set canvas size
                video.onloadedmetadata = () => {
                    canvas.width = video.videoWidth;
                    canvas.height = video.videoHeight;
                    streaming = true;
                    status.innerText = `Camera Active: ${canvas.width}x${canvas.height}`;
                };

                // Start Sending Frames
                setInterval(sendFrame, 1000); // 1 FPS is enough for parking
            } catch (err) {
                status.innerText = "Error: " + err;
                status.style.color = "red";
            }
        }

        function sendFrame() {
            if (!streaming) return;
            
            // Draw full-size frame to hidden canvas
            context.drawImage(video, 0, 0, canvas.width, canvas.height);
            
            // Convert to JPEG Blob and Upload
            canvas.toBlob(blob => {
                const formData = new FormData();
                formData.append('file', blob, 'frame.jpg');

                fetch('/upload', { method: 'POST', body: formData })
                    .then(res => {
                        if(res.ok) status.innerText = `🟢 Live: ${canvas.width}x${canvas.height}`;
                    })
                    .catch(err => status.innerText = "⚠️ Upload Error");
            }, 'image/jpeg', 0.7); // 0.7 Quality (Good balance)
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