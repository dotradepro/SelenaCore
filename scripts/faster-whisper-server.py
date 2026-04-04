#!/usr/bin/env python3
"""Faster-Whisper HTTP server with CUDA — runs inside jetson-containers.

Provides the same /inference API as whisper.cpp server,
so SelenaCore's WhisperCppProvider works without changes.

Usage inside container:
    python3 /app/faster-whisper-server.py --model small --port 9000

API:
    GET  /          → health check JSON
    POST /inference → multipart WAV file → JSON {text, language, detected_language}
"""
import argparse
import io
import json
import logging
import wave

from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

model = None
model_name = "small"


def load_model(name: str, device: str = "auto", compute_type: str = "auto"):
    from faster_whisper import WhisperModel

    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    logger.info("Loading faster-whisper model=%s device=%s compute=%s", name, device, compute_type)
    m = WhisperModel(name, device=device, compute_type=compute_type)
    logger.info("Model loaded successfully")
    return m


class InferenceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "model": model_name}).encode())

    def do_POST(self):
        if self.path != "/inference":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        content_type = self.headers.get("Content-Type", "")
        wav_data = None

        language = None
        if "multipart" in content_type:
            wav_data, language = self._extract_multipart(body, content_type)
        else:
            wav_data = body

        if not wav_data:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":"no audio"}')
            return

        try:
            # Read optional settings from form fields
            def _field(name, default, cast=str):
                val = self._extract_form_field(body, content_type, name) if "multipart" in content_type else None
                if val is None:
                    return default
                try:
                    return cast(val)
                except Exception:
                    return default

            beam_size = _field("beam_size", 5, int)
            temperature = _field("temperature", 0.0, float)
            no_speech_threshold = _field("no_speech_threshold", 0.6, float)
            vad_filter = _field("vad_filter", "true", str).lower() in ("true", "1", "yes")
            vad_min_silence_ms = _field("vad_min_silence_ms", 500, int)
            vad_speech_pad_ms = _field("vad_speech_pad_ms", 400, int)
            vad_threshold = _field("vad_threshold", 0.5, float)
            condition_prev = _field("condition_on_previous_text", "false", str).lower() in ("true", "1", "yes")

            wav_buf = io.BytesIO(wav_data)
            segments, info = model.transcribe(
                wav_buf,
                language=language,
                beam_size=beam_size,
                temperature=temperature,
                vad_filter=vad_filter,
                vad_parameters=dict(
                    min_silence_duration_ms=vad_min_silence_ms,
                    speech_pad_ms=vad_speech_pad_ms,
                    threshold=vad_threshold,
                ),
                condition_on_previous_text=condition_prev,
                no_speech_threshold=no_speech_threshold,
            )

            text = " ".join(seg.text.strip() for seg in segments).strip()

            if text in ("[BLANK_AUDIO]", "(BLANK_AUDIO)", "[silence]"):
                text = ""

            lang = info.language or "en"
            result = {"text": text, "language": lang, "detected_language": lang}

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except Exception as e:
            logger.error("Transcription error: %s", e)
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _extract_multipart(self, body: bytes, content_type: str) -> tuple:
        """Extract WAV file and language from multipart form data."""
        boundary = content_type.split("boundary=")[-1].encode()
        parts = body.split(b"--" + boundary)
        wav_data = None
        language = None
        for part in parts:
            if b"filename=" in part:
                idx = part.find(b"\r\n\r\n")
                if idx >= 0:
                    wav_data = part[idx + 4:].rstrip(b"\r\n--")
            elif b'name="language"' in part:
                idx = part.find(b"\r\n\r\n")
                if idx >= 0:
                    lang_val = part[idx + 4:].strip().rstrip(b"\r\n--").decode(errors="ignore").strip()
                    if lang_val and lang_val != "auto":
                        language = lang_val
        return wav_data, language

    def _extract_form_field(self, body: bytes, content_type: str, field: str) -> str | None:
        """Extract a named form field value from multipart data."""
        boundary = content_type.split("boundary=")[-1].encode()
        parts = body.split(b"--" + boundary)
        needle = f'name="{field}"'.encode()
        for part in parts:
            if needle in part:
                idx = part.find(b"\r\n\r\n")
                if idx >= 0:
                    return part[idx + 4:].strip().rstrip(b"\r\n--").decode(errors="ignore").strip()
        return None

    def log_message(self, format, *args):
        pass


def main():
    global model, model_name

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="small")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="auto")
    args = parser.parse_args()

    model_name = args.model
    model = load_model(args.model, args.device, args.compute_type)

    server = HTTPServer((args.host, args.port), InferenceHandler)
    logger.info("Faster-Whisper server on http://%s:%d (model=%s, CUDA)", args.host, args.port, args.model)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
