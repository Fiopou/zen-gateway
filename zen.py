#!/usr/bin/env python3
# language: Python 3, file: zen.py, target: Termux/Windows/Linux
# Zen gateway: прямой клиент бесплатных моделей opencode zen + OpenAI-совместимый шлюз.
# Контракт: POST https://opencode.ai/zen/v1/chat/completions, auth "Bearer public" (аноним).
# Лимит zen: ДНЕВНОЙ, по IP, сброс в полночь UTC. Новые IP (первые 7 дней) - двойная норма.
# Обход: смена IP (мобильная сеть/VPN/прокси-пул ZEN_PROXIES).

import argparse
import json
import os
import random
import sys
import time
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.environ.get("ZEN_BASE", "https://opencode.ai/zen/v1")
MODELS_URL = f"{BASE}/models"
CHAT_URL = f"{BASE}/chat/completions"
TIMEOUT = int(os.environ.get("ZEN_TIMEOUT", "120"))
UA = "opencode/1.18.16"

DEFAULT_MODELS = [
    "deepseek-v4-flash-free",
    "hy3-free",
    "big-pickle",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "laguna-s-2.1-free",
    "mimo-v2.5-free",
]


def proxy_for():
    """Случайный прокси из ZEN_PROXIES (http://host:port), иначе None."""
    raw = os.environ.get("ZEN_PROXIES", "").strip()
    if not raw:
        return None
    proxies = [p.strip() for p in raw.split(",") if p.strip()]
    if not proxies:
        return None
    return random.choice(proxies)


def _auth_header():
    """ZEN_API_KEY - реальный ключ console.opencode.ai: открывает платные модели.
    Без ключа - анонимный доступ ("public") к free-моделям."""
    key = os.environ.get("ZEN_API_KEY", "").strip()
    return f"Bearer {key}" if key else "Bearer public"


def _request(url, payload, stream=False, proxy=None, headers_extra=None):
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
        "x-opencode-session": payload.get("_session", "zenpy-" + str(os.getpid())),
        "x-opencode-client": "zen.py/1.0",
        "User-Agent": UA,
    }
    if headers_extra:
        headers.update(headers_extra)
    body = payload.copy()
    body.pop("_session", None)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    opener = urllib.request.build_opener()
    if proxy:
        opener.add_handler(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    try:
        return opener.open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "ignore")[:500]
        raise ZenError(e.code, err, e.headers.get("retry-after"))
    except urllib.error.URLError as e:
        raise ZenError(0, f"network: {e.reason}", None)


class ZenError(Exception):
    def __init__(self, code, body, retry_after=None):
        self.code = code
        self.body = body
        self.retry_after = retry_after
        super().__init__(f"HTTP {code}: {body[:200]}")

    def is_rate_limited(self):
        return self.code in (429, 529) or "rate limit" in self.body.lower() or "RateLimit" in self.body


def chat(model, messages, stream=False, session=None):
    """Одиночный вызов. Возвращает dict ответа (не-стрим) или итератор чанков."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "_session": session or ("zenpy-" + str(random.randint(100000, 999999))),
    }
    resp = _request(CHAT_URL, payload, stream=stream)
    if not stream:
        return json.loads(resp.read().decode("utf-8"))
    return _iter_sse(resp)


def _iter_sse(resp):
    """SSE: yield (json, done)."""
    buf = b""
    for raw in resp:
        buf += raw
        while b"\n\n" in buf:
            chunk, buf = buf.split(b"\n\n", 1)
            for line in chunk.split(b"\n"):
                line = line.strip()
                if not line or not line.startswith(b"data:"):
                    continue
                item = line[5:].strip()
                if item == b"[DONE]":
                    yield None, True
                    return
                try:
                    yield json.loads(item.decode("utf-8")), False
                except json.JSONDecodeError:
                    continue
    yield None, True


def list_models():
    req = urllib.request.Request(MODELS_URL, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
            return [m.get("id") or m.get("model") or m for m in (data.get("data") or data.get("models") or [])]
    except Exception:
        return DEFAULT_MODELS


def estimate_tokens(messages):
    """Грубая оценка: ~3 символа на токен (кириллица/латиница)."""
    return sum(len(str(m.get("content", ""))) // 3 for m in messages)


def _summarize(text):
    prompt = (
        "Сожми текст в краткое резюме. Сохрани все факты, код, имена, числа "
        "и незавершённые задачи. Числа, коды, имена и ключевые термины перечисли "
        "списком, ничего не теряя. Только резюме, без пояснений.\n\n" + text
    )
    resp = chat(DEFAULT_MODELS[0], [{"role": "user", "content": prompt}], stream=False)
    return (resp.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def compact_if_needed(messages, limit=None):
    """Авто-расширение контекста: при превышении порога сжимает историю
    в резюме через ту же модель (чанками, чтобы не упираться в таймауты).
    Работает для любой модели шлюза."""
    limit = limit or int(os.environ.get("ZEN_MAX_CONTEXT", "150000"))
    if estimate_tokens(messages) <= limit:
        return messages
    chunk_size = int(os.environ.get("ZEN_COMPACT_CHUNK", "25000"))
    for _ in range(3):
        if estimate_tokens(messages) <= limit:
            break
        keep = messages[-4:]
        history = messages[:-4]
        text = "\n".join(f"{m.get('role')}: {str(m.get('content'))}" for m in history)
        chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
        summaries = []
        for c in chunks:
            try:
                s = _summarize(c)
            except ZenError:
                return messages
            if s:
                summaries.append(s)
        if not summaries:
            return messages
        messages = [{"role": "system", "content": "Резюме прошлого диалога: " + " ".join(summaries)}] + keep
    return messages


def run_interactive(args):
    model = args.model or DEFAULT_MODELS[0]
    limit = int(os.environ.get("ZEN_MAX_CONTEXT", "150000"))
    messages = []
    print(f"zen chat, model={model}, max_context={limit} chars, /model <name> /new /quit")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("/quit", "/q", "/exit"):
            return
        if line == "/new":
            messages = []
            print("(history cleared)")
            continue
        if line.startswith("/model "):
            model = line.split(" ", 1)[1].strip()
            print(f"(model -> {model})")
            continue
        messages.append({"role": "user", "content": line})
        messages = compact_if_needed(messages, limit)
        try:
            for chunk, done in chat(model, messages, stream=True):
                if done:
                    break
                for c in chunk.get("choices", []):
                    d = c.get("delta", {})
                    if d.get("reasoning_content"):
                        print(f"[think] {d['reasoning_content']}", file=sys.stderr, flush=True)
                    if d.get("content"):
                        print(d["content"], end="", flush=True)
            print()
        except ZenError as e:
            _report_rate(e)
            continue


def run_cli(args):
    if args.prompt is None:
        run_interactive(args)
        return
    model = args.model or DEFAULT_MODELS[0]
    messages = [{"role": "user", "content": args.prompt}]
    if args.stream:
        try:
            for chunk, done in chat(model, messages, stream=True):
                if done:
                    break
                for c in chunk.get("choices", []):
                    d = c.get("delta", {})
                    if d.get("reasoning_content"):
                        print(f"[think] {d['reasoning_content']}", file=sys.stderr, flush=True)
                    if d.get("content"):
                        print(d["content"], end="", flush=True)
            print()
        except ZenError as e:
            _report_rate(e)
            sys.exit(2)
    else:
        try:
            resp = chat(model, messages)
        except ZenError as e:
            _report_rate(e)
            sys.exit(2)
        msg = resp["choices"][0]["message"]
        if msg.get("reasoning_content"):
            print(f"[think] {msg['reasoning_content']}", file=sys.stderr)
        print(msg.get("content") or "")


def _report_rate(e):
    print(f"ZEN ERROR {e.code}: {e.body[:300]}", file=sys.stderr)
    if e.is_rate_limited():
        hint = "Дневной IP-лимит. Смени сеть/VPN или переключи прокси (ZEN_PROXIES)."
        if e.retry_after:
            hint += f" retry-after: {e.retry_after}s"
        print(hint, file=sys.stderr)


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "ZenGateway/1.0"
    max_context = int(os.environ.get("ZEN_MAX_CONTEXT", "150000"))
    session_id = "gw-" + os.urandom(6).hex()

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, x-api-key")
        self.send_header("Access-Control-Max-Age", "86400")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _auth(self):
        token = os.environ.get("ZEN_TOKEN", "")
        if token:
            header = self.headers.get("Authorization", "")
            if header != f"Bearer {token}" and self.headers.get("x-api-key") != token:
                self.send_error(401, "Unauthorized")
                return False
        return True

    def _reply(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._auth():
            return
        path = self.path.split("?")[0].rstrip("/")
        if path.endswith("/models"):
            self._reply(200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "zen"} for m in list_models()]})
        else:
            self._reply(404, {"error": {"message": f"not found: {self.path}", "type": "invalid_request_error", "code": 404}})

    def do_POST(self):
        if not self._auth():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            self._reply(400, {"error": {"message": "invalid json", "type": "invalid_request_error", "code": 400}})
            return
        path = self.path.split("?")[0].rstrip("/")
        if "chat/completions" not in path and path not in ("", "/v1", "/v1beta"):
            self._reply(404, {"error": {"message": f"not found: {self.path}", "type": "invalid_request_error", "code": 404}})
            return
        model = body.get("model") or DEFAULT_MODELS[0]
        messages = body.get("messages") or []
        stream = bool(body.get("stream", False))
        session = body.get("_session") or GatewayHandler.session_id
        limit = self.max_context
        if limit and estimate_tokens(messages) > limit:
            messages = compact_if_needed(messages, limit)
        try:
            if stream:
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                resp_id = "chatcmpl-zen-" + str(random.randint(1000000, 9999999))
                for chunk, done in chat(model, messages, stream=True, session=session):
                    if done:
                        break
                    out = {
                        "id": resp_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": chunk.get("choices", []),
                    }
                    self.wfile.write(f"data: {json.dumps(out)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                resp = chat(model, messages, stream=False, session=session)
                self._reply(200, resp)
        except ZenError as e:
            self._reply(e.code if e.code else 502, {"error": {"message": e.body[:400], "type": "api_error", "code": e.code or 502}})
        except Exception as e:
            self._reply(500, {"error": {"message": str(e)[:400], "type": "api_error", "code": 500}})


def run_gateway(args):
    port = args.port
    GatewayHandler.max_context = args.max_context
    httpd = ThreadingHTTPServer(("0.0.0.0", port), GatewayHandler)
    print(f"zen gateway on 0.0.0.0:{port}  (POST /v1/chat/completions, GET /v1/models)")
    print(f"auth: {'ZEN_TOKEN required' if os.environ.get('ZEN_TOKEN') else 'open'}")
    httpd.serve_forever()


def main():
    ap = argparse.ArgumentParser(description="zen.py - opencode zen client + OpenAI gateway")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("chat", help="разовый запрос")
    c.add_argument("prompt", nargs="?", default=None)
    c.add_argument("-m", "--model", default=None)
    c.add_argument("--stream", action="store_true")
    g = sub.add_parser("serve", help="поднять OpenAI-совместимый шлюз")
    g.add_argument("--port", type=int, default=int(os.environ.get("ZEN_PORT", "8787")))
    g.add_argument("--max-context", type=int, default=int(os.environ.get("ZEN_MAX_CONTEXT", "150000")),
                   help="порог авто-компакта в символах (0 = выключить)")
    m = sub.add_parser("models", help="список моделей zen")
    args = ap.parse_args()

    if args.cmd == "models":
        for m in list_models():
            print(m)
    elif args.cmd == "serve":
        run_gateway(args)
    else:
        run_cli(args)


if __name__ == "__main__":
    main()
