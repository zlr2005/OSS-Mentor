"""Receive one base64 gzip line on stdin, validate it, and save without overwriting."""
import argparse
import base64
import gzip
import hashlib
import json
import sys
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets

from pathlib import Path

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    p.add_argument('--raw-deflate',action='store_true')
    p.add_argument('--http',action='store_true',help='Bounded localhost transfer; fixed destination only')
    args=p.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        p.error('Existing output or missing parent directory')
    if args.http:
        token=secrets.token_urlsafe(24)
        chunks=[]
        total=[0]
        class Receiver(BaseHTTPRequestHandler):
            def log_message(self,*_):
                pass
            def do_POST(self):
                if self.headers.get('X-Transfer-Key') != token:
                    self.send_error(403);return
                size=int(self.headers.get('Content-Length','0'))
                if not 0 <= size <= 60000 or total[0]+size > 100_000_000:
                    self.send_error(413);return
                if self.path == '/chunk':
                    data=self.rfile.read(size).decode('ascii')
                    if any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in data):
                        self.send_error(400);return
                    chunks.append(data);total[0]+=size
                    self.send_response(200);self.end_headers();self.wfile.write(b'ok')
                elif self.path == '/finish':
                    data=base64.b64decode(''.join(chunks),validate=True)
                    raw=zlib.decompress(data,-15) if args.raw_deflate else gzip.decompress(data)
                    json.loads(raw)
                    compressed=gzip.compress(raw)
                    with args.output.open('xb') as stream:
                        stream.write(compressed)
                    self.send_response(200);self.end_headers();self.wfile.write(b'saved')
                    print(json.dumps(dict(saved=str(args.output),bytes=len(compressed),sha256=hashlib.sha256(raw).hexdigest())),flush=True)
                    self.server.finished=True
                else:
                    self.send_error(404)
        server=HTTPServer(('127.0.0.1',0),Receiver)
        server.finished=False
        server.timeout=60
        print(json.dumps(dict(port=server.server_address[1],transfer_key=token)),flush=True)
        try:
            while not server.finished:
                server.handle_request()
        finally:
            server.server_close()
        sys.exit(0)
    # The PTY keeps stdin open. Disable echo and console line-length limits while
    # receiving public evidence; restore the original mode before leaving.
    import ctypes
    console=ctypes.windll.kernel32
    handle=console.GetStdHandle(-10)
    mode=ctypes.c_uint()
    got_mode=console.GetConsoleMode(handle,ctypes.byref(mode))
    if got_mode:
        console.SetConsoleMode(handle,mode.value & ~6)
    print('READY: expecting base64 gzip evidence line',flush=True)
    try:
        line=sys.stdin.readline(100_000_000).strip()
    finally:
        if got_mode:
            console.SetConsoleMode(handle,mode.value)
    compressed=base64.b64decode(line,validate=True)
    raw=zlib.decompress(compressed,-15) if args.raw_deflate else gzip.decompress(compressed)
    json.loads(raw)
    if args.raw_deflate:
        compressed=gzip.compress(raw)
    with args.output.open('xb') as stream:
        stream.write(compressed)
    print(json.dumps(dict(saved=str(args.output),bytes=len(compressed),sha256=hashlib.sha256(raw).hexdigest())),flush=True)
