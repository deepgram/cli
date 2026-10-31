"""Fake speech API for the data-path bench rows. Stdlib only, loopback only.

Serves the prerecorded transcription shape the CLI decodes, plus the project
listing some versions call before a request. It never forwards anything.

Env: FAKE_PORT (8080), WORDS (words in the transcript, default 50).
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def transcript_json(n_words):
    words, t = [], 0.0
    for i in range(n_words):
        w = "word%d" % i
        words.append(
            {
                "word": w,
                "start": t,
                "end": t + 0.3,
                "confidence": 0.99,
                "punctuated_word": w,
                "speaker": i // 50 % 2,
            }
        )
        t += 0.35
    text = " ".join(w["word"] for w in words)
    return {
        "metadata": {
            "request_id": "fake",
            "duration": t,
            "channels": 1,
            "model_info": {"m": {"name": "nova-3", "version": "fake"}},
        },
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {"transcript": text, "confidence": 0.99, "words": words}
                    ]
                }
            ]
        },
    }


BODY = json.dumps(transcript_json(int(os.environ.get("WORDS", "50")))).encode()
PROJECTS = json.dumps(
    {
        "projects": [
            {"project_id": "00000000-0000-0000-0000-000000000000", "name": "fake"}
        ]
    }
).encode()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _drain(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    return
                left = size
                while left:
                    left -= len(self.rfile.read(min(left, 1 << 20)))
                self.rfile.readline()
        left = int(self.headers.get("Content-Length") or 0)
        while left > 0:
            n = len(self.rfile.read(min(left, 1 << 20)))
            if not n:
                return
            left -= n

    def _send(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(PROJECTS if self.path.startswith("/v1/projects") else b"{}")

    def do_POST(self):
        self._drain()
        self._send(BODY if self.path.startswith("/v1/listen") else b"{}")


if __name__ == "__main__":
    ThreadingHTTPServer(
        ("127.0.0.1", int(os.environ.get("FAKE_PORT", "8080"))), H
    ).serve_forever()
