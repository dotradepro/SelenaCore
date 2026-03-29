"""
Piper TTS GPU server — lightweight HTTP wrapper.
Runs inside a Jetson container with onnxruntime-gpu.
Selena-core calls this instead of local piper binary.

Endpoints:
  POST /synthesize  {text, voice, length_scale, noise_scale, noise_w_scale, sentence_silence, volume, speaker}
  GET  /health
  GET  /voices
"""
import json
import os
import subprocess
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

MODELS_DIR = os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper")
PORT = int(os.environ.get("PIPER_PORT", "5100"))


class PiperHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "gpu": True}).encode())
        elif self.path == "/voices":
            voices = []
            p = Path(MODELS_DIR)
            if p.is_dir():
                for f in sorted(p.iterdir()):
                    if f.suffix == ".onnx":
                        voices.append({"id": f.stem, "file": f.name})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"voices": voices}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/synthesize":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        text = body.get("text", "")
        voice = body.get("voice", "")
        if not text or not voice:
            self.send_response(422)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "text and voice required"}).encode())
            return

        model_path = os.path.join(MODELS_DIR, f"{voice}.onnx")
        if not os.path.exists(model_path):
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"voice not found: {voice}"}).encode())
            return

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            cmd = [
                "piper", "--model", model_path, "--output_file", tmp_path,
                "--cuda",
                "--length-scale", str(body.get("length_scale", 1.0)),
                "--noise-scale", str(body.get("noise_scale", 0.667)),
                "--noise-w-scale", str(body.get("noise_w_scale", 0.8)),
                "--sentence-silence", str(body.get("sentence_silence", 0.2)),
                "--volume", str(body.get("volume", 1.0)),
                "--speaker", str(body.get("speaker", 0)),
            ]
            result = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=30)
            if result.returncode != 0:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": result.stderr.decode()[:200]}).encode())
                return

            wav_bytes = Path(tmp_path).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav_bytes)))
            self.end_headers()
            self.wfile.write(wav_bytes)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def log_message(self, format, *args):
        pass  # suppress access logs


if __name__ == "__main__":
    print(f"[piper-gpu] Starting on port {PORT}, models: {MODELS_DIR}")
    try:
        import onnxruntime
        print(f"[piper-gpu] onnxruntime {onnxruntime.__version__}, providers: {onnxruntime.get_available_providers()}")
    except Exception:
        pass
    server = HTTPServer(("0.0.0.0", PORT), PiperHandler)
    server.serve_forever()
