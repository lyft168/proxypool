#!/usr/bin/env python3
"""Minimal HTTP server for the proxy list files.

Serves only the proxy lists and the country index — never account
credentials. Configure with SERVE_HOST / SERVE_PORT / BASE_DIR (.env).
"""
import http.server, os, re, socketserver
from pathlib import Path

BASE = Path(os.environ.get("BASE_DIR") or Path(__file__).resolve().parent)
HOST = os.environ.get("SERVE_HOST", "0.0.0.0")
PORT = int(os.environ.get("SERVE_PORT", "8126"))


def allowed(name):
    if name in ("health", "proxy_list.txt", "gologin_proxies.txt", "proxies_collected.txt"):
        return True
    return bool(re.fullmatch(r"proxy_[A-Z]{2}\.txt", name))


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.strip("/").split("?")[0]
        if not allowed(name):
            self.send_response(404); self.end_headers(); self.wfile.write(b"not found"); return
        if name == "health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        try:
            with open(BASE / name, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            self.send_response(404); self.end_headers(); self.wfile.write(b"empty"); return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer((HOST, PORT), H) as s:
    s.serve_forever()
