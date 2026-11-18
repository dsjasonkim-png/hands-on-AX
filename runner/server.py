"""『Hands-on AX』 예제 실행기 서버.

    python runner/server.py            # 브라우저에서 http://127.0.0.1:8765 를 엽니다
    python runner/server.py --port 9000 --no-browser

표준 라이브러리만 씁니다. 장마다 작업 프로세스(worker.py)를 하나씩 띄워 셀을 이어서 실행합니다.

보안: 이 서버는 내 컴퓨터의 코드를 실행합니다. 그래서
- 이 컴퓨터(127.0.0.1)에서만 접속을 받고,
- 주소창의 호스트가 127.0.0.1·localhost가 아니면 거절하고(DNS 리바인딩 방지),
- 서버를 켤 때마다 새로 만드는 토큰을 가진 요청만 실행합니다(다른 웹사이트가 몰래 보내는 요청 차단).
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent
BOOK_DIR = RUNNER_DIR.parent
sys.path.insert(0, str(RUNNER_DIR))
import chapters as chapter_files  # noqa: E402

CHAPTERS = BOOK_DIR / "chapters"
STATIC = RUNNER_DIR / "static"
LOGS = BOOK_DIR / "logs"
ENV_FILE = BOOK_DIR / ".env"
RUN_TIMEOUT = 900
TOKEN = secrets.token_urlsafe(24)
PORT = 8765

# 화면에서 저장할 수 있는 설정. 값은 .env에만 쓰고 화면으로 돌려보내지 않는다.
SECRET_KEYS = ["OPENAI_API_KEY", "LANGSMITH_API_KEY"]
PLAIN_KEYS = ["LANGSMITH_TRACING", "LANGSMITH_PROJECT"]


# ── .env ─────────────────────────────────────
def read_env() -> dict:
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def write_env(updates: dict) -> None:
    """바꾼 키만 고치고 나머지 줄(주석 포함)은 그대로 둔다. 빈 값은 그 키를 지운다."""
    lines = ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines() if ENV_FILE.exists() else []
    done, out = set(), []
    for line in lines:
        s = line.strip()
        k = s.split("=", 1)[0].strip() if "=" in s and not s.startswith("#") else None
        if k in updates:
            done.add(k)
            if updates[k]:
                out.append(f"{k}={updates[k]}")
            continue
        out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done and v]
    tmp = ENV_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_FILE)


def key_status() -> dict:
    env = read_env()
    has = lambda k: bool(env.get(k) or os.environ.get(k))  # noqa: E731
    return {
        "openai_key": has("OPENAI_API_KEY"),
        "langsmith_key": has("LANGSMITH_API_KEY"),
        "langsmith_tracing": (env.get("LANGSMITH_TRACING") or os.environ.get("LANGSMITH_TRACING", "")).lower() == "true",
        "langsmith_project": env.get("LANGSMITH_PROJECT") or os.environ.get("LANGSMITH_PROJECT", ""),
    }


# ── 장별 작업 프로세스 ──────────────────────────
class Worker:
    def __init__(self, cid: str, mode: str):
        ch = chapter_files.load(CHAPTERS, cid)
        self.cid, self.mode = cid, mode
        workdir = (BOOK_DIR / ch["workdir"]).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        assets = CHAPTERS / cid                  # chapters/<id>/ 의 파일(서버 스크립트 등)을 작업 폴더에 넣는다
        if assets.is_dir():
            shutil.copytree(assets, workdir, dirs_exist_ok=True)
        LOGS.mkdir(exist_ok=True)
        self.log = open(LOGS / f"{cid}.log", "a", encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(RUNNER_DIR / "worker.py"), str(workdir), mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding="utf-8", errors="replace", env=env, cwd=str(workdir),
        )
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.lock = threading.Lock()
        self.count = 0
        try:
            first = self.lines.get(timeout=180)
        except queue.Empty:
            first = None
        if first is None:
            self.kill()
            raise RuntimeError(f"셀 실행 프로세스를 시작하지 못했습니다. logs/{cid}.log 파일을 확인하세요.")

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def run(self, code: str) -> dict:
        with self.lock:
            self.count += 1
            self.proc.stdin.write(json.dumps({"id": self.count, "code": code}, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            try:
                line = self.lines.get(timeout=RUN_TIMEOUT)
            except queue.Empty:
                self.kill()
                return _error("Timeout", f"{RUN_TIMEOUT}초 안에 끝나지 않아 실행을 멈췄습니다.")
            if line is None:
                return _error("실행 중단", "셀 실행 프로세스가 종료됐습니다. 중지 버튼을 눌렀다면 정상입니다. "
                              "아니라면 커널을 다시 시작하고 logs 폴더의 로그를 확인하세요.")
            return json.loads(line)

    def kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass


def _error(name, value, tb=""):
    return {"ok": False, "outputs": [{"type": "error", "ename": name, "evalue": value, "traceback": tb}]}


WORKERS: dict[str, Worker] = {}
WLOCK = threading.Lock()


def get_worker(cid: str, mode: str) -> Worker:
    with WLOCK:
        w = WORKERS.get(cid)
        if w and (not w.alive() or w.mode != mode):
            w.kill()
            w = None
        if not w:
            w = WORKERS[cid] = Worker(cid, mode)
        return w


def stop_worker(cid: str):
    with WLOCK:
        w = WORKERS.pop(cid, None)
    if w:
        w.kill()


def stop_all():
    for cid in list(WORKERS):
        stop_worker(cid)


# ── HTTP ─────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, status: int, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _api_ok(self) -> bool:
        if self.headers.get("X-Runner-Token") != TOKEN:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "127.0.0.1 주소로 접속하세요."})
        path = self.path.split("?")[0]
        if path in ("/", "/index.html") or path.startswith("/ch/"):
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__RUNNER_TOKEN__", TOKEN)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if not path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._api_ok():
            return self._send(403, {"error": "토큰이 맞지 않습니다. 페이지를 새로 고치세요."})
        if path == "/api/chapters":
            items = []
            for ch in chapter_files.load_all(CHAPTERS):
                codes = [c for c in ch["cells"] if c["type"] == "code"]
                items.append({k: ch[k] for k in ("id", "part", "title", "summary", "needs")} |
                             {"code_cells": len(codes),
                              "running": ch["id"] in WORKERS and WORKERS[ch["id"]].alive()})
            return self._send(200, items)
        if path.startswith("/api/chapter/"):
            try:
                return self._send(200, chapter_files.load(CHAPTERS, path.rsplit("/", 1)[1]))
            except KeyError:
                return self._send(404, {"error": "없는 장입니다."})
            except ValueError as e:
                return self._send(500, {"error": str(e)})
        if path == "/api/status":
            return self._send(200, {"python": sys.version.split()[0], **key_status()})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok() or not self._api_ok():
            return self._send(403, {"error": "토큰이 맞지 않습니다. 페이지를 새로 고치세요."})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "JSON 형식이 아닙니다."})
        path = self.path.split("?")[0]
        if path == "/api/run":
            cid, mode = str(req.get("ch", "")), req.get("mode", "real")
            if mode not in ("real", "fake"):
                return self._send(400, {"error": "mode는 real 또는 fake"})
            try:
                w = get_worker(cid, mode)
            except KeyError:
                return self._send(404, {"error": "없는 장입니다."})
            except Exception as e:
                return self._send(200, _error(type(e).__name__, str(e)))
            t0 = time.time()
            res = w.run(str(req.get("code", "")))
            res["seconds"] = round(time.time() - t0, 2)
            return self._send(200, res)
        if path in ("/api/restart", "/api/stop"):
            stop_worker(str(req.get("ch", "")))
            return self._send(200, {"ok": True})
        if path == "/api/keys":
            updates = {}
            for k in SECRET_KEYS + PLAIN_KEYS:
                if k in req:
                    v = str(req[k]).strip()
                    if any(c in v for c in "\r\n\"'") or len(v) > 400:
                        return self._send(400, {"error": f"{k} 값에 쓸 수 없는 문자가 있습니다."})
                    updates[k] = v
            write_env(updates)
            for k, v in updates.items():           # 새 값을 이 서버와 새로 띄울 셀 프로세스에 반영
                if v:
                    os.environ[k] = v
                else:
                    os.environ.pop(k, None)
            stop_all()
            return self._send(200, key_status())
        self._send(404, {"error": "not found"})


def free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit(f"{start}~{start + 19}번 포트가 모두 사용 중입니다. --port로 다른 번호를 지정하세요.")


def main():
    global PORT
    if sys.version_info < (3, 11):
        print(f"[경고] 지금 파이썬은 {sys.version.split()[0]}입니다. 예제 일부가 3.11 이상에서만 동작합니다(3.12 권장).")
    ap = argparse.ArgumentParser(description="Hands-on AX 예제 실행기")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    PORT = free_port(args.port)
    for k, v in read_env().items():               # .env 값을 서버 환경에도 넣어 둔다(셀 프로세스가 물려받음)
        os.environ.setdefault(k, v)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"예제 실행기: {url}  (끝내려면 이 창에서 Ctrl+C)")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_all()


if __name__ == "__main__":
    main()
