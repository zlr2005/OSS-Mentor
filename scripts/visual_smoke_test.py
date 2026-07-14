"""Render the local MVP UI with Edge for repeatable visual smoke testing."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oss_mentor.api import RecommendationApi, make_handler  # noqa: E402
from oss_mentor.sqlite_store import SQLiteCandidateStore  # noqa: E402


def find_edge() -> Path:
    candidates = (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Microsoft Edge was not found")


def render(edge: Path, url: str, output: Path, width: int, height: int) -> None:
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(2):
        profile = ROOT / "data" / f"edge-profile-{uuid4().hex}"
        output.unlink(missing_ok=True)
        command = [
            str(edge),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--no-first-run",
            "--hide-scrollbars",
            f"--window-size={width},{height}",
            "--virtual-time-budget=6000",
            f"--user-data-dir={profile}",
            f"--screenshot={output}",
            url,
        ]
        last_result = subprocess.run(command, check=False, capture_output=True, text=True)
        if output.is_file() and output.stat().st_size > 0:
            return
        if attempt == 0:
            time.sleep(1)
    raise RuntimeError(
        f"Edge failed to render {url}: exit={last_result.returncode}, stderr={last_result.stderr}"
    )


def main() -> int:
    store = SQLiteCandidateStore(
        ROOT / "data" / "oss_mentor.sqlite3",
        ROOT / "db" / "sqlite" / "001_mvp.sql",
    )
    if not store.database_path.is_file():
        raise RuntimeError("Run candidate sync and profile import before visual testing")
    store.initialize()
    api = RecommendationApi(store)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(api, static_root=ROOT / "web")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    base_url = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
            health = json.load(response)
        if health.get("status") != "ok" or not health.get("database_ready"):
            raise RuntimeError(f"API health check failed: {health}")
        custom_payload = json.dumps(
            {
                "limit": 5,
                "feedback_client_id": "12345678-1234-4234-8234-123456789abc",
                "profile": {
                    "display_name": "视觉测试画像",
                    "service_track": "newcomer",
                    "preferred_languages": ["Python"],
                    "operating_systems": ["macos"],
                    "preferred_task_types": ["bug_fix", "testing"],
                    "max_code_difficulty": 1,
                    "max_setup_difficulty": 2,
                    "desired_skill_stretch": 0,
                    "skills": {"python": 1, "testing": 1, "git": 1},
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/api/v1/recommendations/custom",
            data=custom_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            custom_response = json.load(response)
        if custom_response.get("profile_persisted") is not False:
            raise RuntimeError(f"Custom profile API check failed: {custom_response}")
        if not custom_response.get("items") or not custom_response.get("feedback_context"):
            raise RuntimeError(f"Custom feedback context check failed: {custom_response}")
        feedback_payload = json.dumps(
            {
                "task_candidate_id": custom_response["items"][0]["task_candidate_id"],
                "feedback_context": custom_response["feedback_context"],
                "feedback_state": "interested",
            }
        ).encode("utf-8")
        feedback_request = urllib.request.Request(
            f"{base_url}/api/v1/feedback",
            data=feedback_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(feedback_request, timeout=5) as response:
            feedback_response = json.load(response)
        if feedback_response.get("feedback", {}).get("feedback_state") != "interested":
            raise RuntimeError(f"Feedback API check failed: {feedback_response}")
        edge = find_edge()
        output_dir = ROOT / "data"
        output_dir.mkdir(parents=True, exist_ok=True)
        desktop = output_dir / "ui_desktop.png"
        mobile = output_dir / "ui_mobile.png"
        custom = output_dir / "ui_custom_profile.png"
        render(edge, base_url, desktop, 1440, 1800)
        # Desktop Edge enforces a minimum outer-window width near 500 px even in
        # headless mode. 500 px still exercises the <=640 px mobile breakpoint.
        render(edge, base_url, mobile, 500, 2300)
        render(edge, f"{base_url}/?mode=custom", custom, 1440, 1400)
        print(
            json.dumps(
                {
                    "health": health,
                    "desktop": {"path": str(desktop), "bytes": desktop.stat().st_size},
                    "mobile": {"path": str(mobile), "bytes": mobile.stat().st_size},
                    "custom": {"path": str(custom), "bytes": custom.stat().st_size},
                    "custom_recommendation_count": custom_response.get("count"),
                    "feedback_state": feedback_response["feedback"]["feedback_state"],
                },
                ensure_ascii=False,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
