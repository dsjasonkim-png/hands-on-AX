"""장 하나의 셀을 이어서 실행하는 작업 프로세스.

server.py가 장마다 하나씩 띄웁니다. 표준 입력으로 받은 JSON 한 줄({"code": ...})을 실행하고
출력을 JSON 한 줄로 돌려줍니다. 노트북처럼 앞 셀에서 만든 변수를 다음 셀이 이어서 씁니다.

    python worker.py <작업 폴더> <real|fake>
"""
from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent
BOOK_DIR = RUNNER_DIR.parent            # 실행기 최상위 폴더 (.env가 있는 곳)


def load_env() -> None:
    """실행기 폴더의 .env를 읽는다. 값은 출력하지 않는다."""
    for env in (BOOK_DIR / ".env",):
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Outputs:
    def __init__(self):
        self.items: list[dict] = []

    def stream(self, name: str, text: str):
        if not text:
            return
        if self.items and self.items[-1].get("type") == "stream" and self.items[-1]["name"] == name:
            self.items[-1]["text"] += text
        else:
            self.items.append({"type": "stream", "name": name, "text": text})


OUT = Outputs()


class Tee(io.TextIOBase):
    def __init__(self, name):
        self.name = name

    def write(self, s):
        OUT.stream(self.name, s)
        return len(s)

    def flush(self):
        pass

    def fileno(self):
        # 하위 프로세스(MCP 서버 등)에 넘길 때는 실제 표준 오류(로그 파일)를 쓴다.
        return sys.__stderr__.fileno()


def display(*objs, **kwargs):
    """IPython.display.display 대신 쓰는 함수. 그림은 PNG로, 나머지는 글자로 보여 준다."""
    for o in objs:
        png = None
        if hasattr(o, "_repr_png_"):
            try:
                png = o._repr_png_()
            except Exception:
                png = None
        if isinstance(png, tuple):          # (데이터, 메타데이터) 형태로 돌려주는 경우
            png = png[0]
        if png:
            data = png if isinstance(png, str) else base64.b64encode(png).decode()
            OUT.items.append({"type": "image", "png": data})
        else:
            OUT.stream("stdout", repr(o) + "\n")


def sh(cmd: str, timeout: int = 900) -> int:
    """명령을 작업 폴더에서 실행하고 출력을 셀 결과로 보여 준다. 'python'은 이 실행기의 파이썬으로 바꾼다."""
    if cmd.startswith("python "):
        cmd = f'"{sys.executable}" ' + cmd[len("python "):]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(cmd, shell=True, cwd=os.getcwd(), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    OUT.stream("stdout", proc.stdout)
    OUT.stream("stderr", proc.stderr)
    if proc.returncode:
        OUT.stream("stderr", f"\n(종료 코드 {proc.returncode})\n")
    return proc.returncode


def patch_ipython():
    try:
        import IPython.display as ipd
        ipd.display = display
    except Exception:
        pass


LOOP = asyncio.new_event_loop()
FLAGS = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
CO_COROUTINE = 0x80


def run_cell(code: str, g: dict):
    tree = ast.parse(code, mode="exec")
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
    for node, mode in ((tree, "exec"), (last, "eval")):
        if node is None:
            continue
        compiled = compile(node, "<셀>", mode, flags=FLAGS)
        if compiled.co_flags & CO_COROUTINE:
            result = LOOP.run_until_complete(eval(compiled, g))
        else:
            result = eval(compiled, g)
        if mode == "eval" and result is not None:
            if hasattr(result, "_repr_png_"):
                display(result)
            else:
                OUT.stream("stdout", repr(result) + "\n")


def main():
    workdir, mode = Path(sys.argv[1]), sys.argv[2]
    workdir.mkdir(parents=True, exist_ok=True)
    os.chdir(workdir)
    sys.path.insert(0, str(workdir))
    sys.path.insert(0, str(RUNNER_DIR))
    # 프로토콜은 원래 표준 출력으로만 보낸다. 라이브러리가 fd 1에 직접 쓰는 것은 표준 오류로 돌린다.
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)
    load_env()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if mode == "fake":
        os.environ["BOOK_RUNNER_FAKE"] = "1"
        os.environ.setdefault("OPENAI_API_KEY", "sk-fake-for-book-runner")
        os.environ["LANGSMITH_TRACING"] = "false"
        # sh()로 띄우는 파이썬도 가짜 모델을 쓰도록 sitecustomize 경로를 넘긴다.
        os.environ["PYTHONPATH"] = str(RUNNER_DIR / "fakesite") + os.pathsep + os.environ.get("PYTHONPATH", "")
        import fakechat
        fakechat.install()
    patch_ipython()
    asyncio.set_event_loop(LOOP)
    # 셀 안의 asyncio.run()도 같은 이벤트 루프에서 돌린다. 루프를 닫아 버리면 모델 클라이언트가 재사용하는
    # 연결이 닫힌 루프에 묶여, 뒤 셀의 await에서 'Event loop is closed' 오류가 난다.
    _orig_run = asyncio.run

    def _run(coro, **kwargs):
        if LOOP.is_running():
            return _orig_run(coro, **kwargs)
        return LOOP.run_until_complete(coro)
    asyncio.run = _run
    # 셀이 만든 클래스의 타입 힌트를 LangGraph가 찾을 수 있도록 실제 __main__ 모듈을 새로 만들어 쓴다.
    import types
    main_mod = types.ModuleType("__main__")
    sys.modules["__main__"] = main_mod
    g = main_mod.__dict__
    g.update({"display": display, "sh": sh, "__builtins__": __builtins__})
    proto.write(json.dumps({"ready": True, "mode": mode}) + "\n")
    proto.flush()
    for line in sys.stdin:
        req = json.loads(line)
        OUT.items = []
        ok = True
        with contextlib.redirect_stdout(Tee("stdout")), contextlib.redirect_stderr(Tee("stderr")):
            try:
                if mode == "fake" and "init_chat_model" in req["code"]:
                    import fakechat
                    fakechat.install()
                run_cell(req["code"], g)
            except SystemExit:
                pass
            except BaseException as e:   # 셀 오류는 결과로 보여 주고 다음 셀을 기다린다
                ok = False
                tb = traceback.format_exception(type(e), e, e.__traceback__)
                tb = [t for t in tb if 'worker.py"' not in t]
                OUT.items.append({"type": "error", "ename": type(e).__name__, "evalue": str(e),
                                  "traceback": "".join(tb)})
            finally:
                if mode == "fake":
                    import fakechat
                    fakechat.install()
        proto.write(json.dumps({"id": req.get("id"), "ok": ok, "outputs": OUT.items}, ensure_ascii=False) + "\n")
        proto.flush()


if __name__ == "__main__":
    main()
