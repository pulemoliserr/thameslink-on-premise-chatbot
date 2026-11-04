"""A fake Ollama server for testing the app without downloading models.

It implements /api/tags, /api/embed and /api/chat with simple word-overlap logic,
so the full pipeline (embeddings, hybrid search, prompting, citations) can be
exercised end to end. It is NOT a language model — answers are crude on purpose.

    python tests/mock_ollama.py --port 11500
    OLLAMA_URL=http://localhost:11500 streamlit run app.py
"""
import argparse
import hashlib
import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 256
STOP = set("a an and are as at be by can do for from has have how i if in is it of on or the to was we what when"
           " which who will with you your".split())


def words(t):
    return [w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP]


def vec(text):
    v = [0.0] * DIM
    for w in words(text):
        w = w[:6]                                    # crude stemming
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send({"models": [{"name": "llama3.1:8b"}, {"name": "nomic-embed-text:latest"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/embed":
            inputs = req["input"] if isinstance(req["input"], list) else [req["input"]]
            self._send({"model": req["model"], "embeddings": [vec(t) for t in inputs]})
        elif self.path == "/api/chat":
            user = req["messages"][-1]["content"]
            question = user.rsplit("):", 1)[-1] if "Question from" in user else user
            sources = re.findall(r'<source n="(\d+)"[^>]*>\n(.*?)\n</source>', user, re.S)
            q = set(w[:6] for w in words(question))
            best = max(sources, key=lambda s: len(q & set(w[:6] for w in words(s[1]))), default=None)
            if not best or len(q & set(w[:6] for w in words(best[1]))) < max(1, len(q) // 3):
                text = "I can’t find this in the documents I have."
            else:
                first = re.split(r"(?<=[.!?])\s", best[1].strip())[0]
                text = f"(mock model) {first} [{best[0]}]"
            self._send({"model": req["model"], "message": {"role": "assistant", "content": text}, "done": True})
        else:
            self._send({"error": "not found"}, 404)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11500)
    args = ap.parse_args()
    print(f"mock Ollama on http://localhost:{args.port}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
