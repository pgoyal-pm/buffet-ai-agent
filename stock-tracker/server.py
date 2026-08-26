"""Simple static file server for stock-tracker."""
import http.server, os, sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
DIR = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw): super().__init__(*a, directory=DIR, **kw)
    def log_message(self, fmt, *args): pass  # quiet

http.server.serve_forever(Handler)
print(f"Stock tracker at http://localhost:{PORT}", flush=True)
