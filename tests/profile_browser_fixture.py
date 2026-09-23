"""Explicit localhost-only browser fixture. Never mounted by the production CLI.

Run: .venv/Scripts/python.exe tests/profile_browser_fixture.py
Uses a disposable DB and mocked GitHub source; no network or real credentials.
"""
from http.server import ThreadingHTTPServer

from test_profile_api import ProfileApiTests, ROOT
from oss_mentor.api import make_handler
from oss_mentor.services.auth_service import SESSION_COOKIE_NAME


def main():
    fixture = ProfileApiTests()
    fixture.setUp()
    base = make_handler(fixture.api, static_root=ROOT / "web")

    class FixtureHandler(base):
        def do_GET(self):
            if self.path == "/test/session":
                self.send_response(303)
                self.send_header("Location", "/profile")
                self.send_header("Set-Cookie", f"{SESSION_COOKIE_NAME}={fixture.session}; Path=/; HttpOnly; SameSite=Lax")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 8876), FixtureHandler)
    fixture.auth.settings.base_url = "http://127.0.0.1:8876"
    print("FIXTURE ONLY: http://127.0.0.1:8876/profile (anonymous); /test/session (test account)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
