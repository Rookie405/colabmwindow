#!/usr/bin/env python3
"""Minimal OpenAI-compatible server for ExLlamaV3 (collabosm).

Why this exists
---------------
exllamav3 v1.5.1 ships NO server: `examples/` contains chat.py / chat_console.py and
nothing that speaks HTTP, and TabbyAPI (the usual front end) does not expose the
parameters this setup depends on -- the pinned-RAM second-tier KV page cache
(`-ccs`), the recurrent checkpoint store (`-rcs`) and the generator chunk size
(`-gcs`). So this is a deliberately small server built on the library the rest of
the kit already uses.

Two details that are easy to get wrong and are load-bearing here:

1. ONE long-lived Generator. The PageTable and the CPU page cache are constructed
   inside Generator.__init__, so a Generator per request silently throws away prompt
   caching and the warm KV tier. This process builds exactly one and keeps it.
2. Requests are serialised. One Generator owns one cache of `cache_size` tokens, and
   `max_num_tokens` is the budget across concurrent jobs, so the lock is not a
   limitation of the server, it is the shape of the engine.

Surface: GET /health, GET /v1/models, POST /v1/chat/completions (stream and
non-stream). That is all on purpose -- this is how you point a client at the
measured-best configuration, not a production gateway.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_DIR = os.environ.get("MODEL_DIR", "/content/exl3")
SRC = os.environ.get("EXL3_SRC", "/content/exllamav3-src")
if os.path.isdir(os.path.join(SRC, "examples")):
    sys.path.insert(0, os.path.join(SRC, "examples"))

from exllamav3 import Generator, model_init  # noqa: E402
from exllamav3.generator.sampler import ComboSampler  # noqa: E402

try:
    import jinja2
    from jinja2.sandbox import ImmutableSandboxedEnvironment
except Exception:
    jinja2 = None

LOCK = threading.Lock()
# Bearer-key check. serve.sh writes the key before starting this process; upstream generated
# it but never enforced it, which left the public trycloudflare URL open to anyone.
KEY_FILE = os.environ.get("API_KEY_FILE", "/content/api-key.txt")
API_KEY = (os.environ.get("API_KEY") or
           (open(KEY_FILE).read().strip() if os.path.exists(KEY_FILE) else ""))
GEN = None
TOK = None
ARGS = None
STOP = None       # stop conditions: EOS ids + <|im_end|>


def build_engine():
    """Load once, with the flags the repository measured as best."""
    global GEN, TOK, ARGS
    parser = argparse.ArgumentParser(allow_abbrev=False)
    model_init.add_args(parser, cache=True, add_sampling_args=True,
                        add_draft_model_args=True,
                        default_cache_size=int(os.environ.get("CACHE_SIZE", 262144)),
                        default_autosplit_max_batch_size=1)
    for flags, kw in [
        (["-mode", "--mode"], dict(type=str, default="chatml")),
        (["-gcs", "--generator_chunk_size"], dict(type=int, default=4096)),
    ]:
        parser.add_argument(*flags, **kw)

    gcs = int(os.environ.get("GCS", 4096))
    ndt = int(os.environ.get("NDT", 4))
    ccs = float(os.environ.get("CPU_CACHE_GB", 0) or 0)
    rcs = float(os.environ.get("RECURRENT_CACHE_GB", 4) or 0)

    argv = ["serve", "-m", MODEL_DIR,
            "-cs", str(os.environ.get("CACHE_SIZE", 262144)),
            "-cq", str(os.environ.get("CACHE_QUANT", "4")),
            "-ngr",                       # n-gram/PLE table in host RAM: mandatory here
            "-mtp", "-ndt", str(ndt),
            "-gcs", str(gcs),
            "-mode", os.environ.get("CHAT_MODE", "chatml")]
    if ccs:
        argv += ["-ccs", str(ccs)]        # pinned-RAM second-tier KV page cache
    if rcs:
        argv += ["-rcs", str(rcs)]        # GDN checkpoint store (host RAM)
    sys.argv = argv
    ARGS = parser.parse_args()

    t0 = time.time()
    loaded = model_init.init(ARGS)
    model, config, cache, tokenizer = loaded[0], loaded[1], loaded[2], loaded[3]
    draft_model, draft_cache = (loaded[4], loaded[6]) if len(loaded) >= 7 else (None, None)
    print("[api] model loaded in %.1fs, cache=%d tokens, draft=%s"
          % (time.time() - t0, cache.max_num_tokens, draft_model is not None), flush=True)

    # ONE Generator for the process lifetime -- see the module docstring.
    GEN = Generator(model=model, cache=cache, tokenizer=tokenizer,
                    draft_model=draft_model, draft_cache=draft_cache,
                    num_draft_tokens=ndt if draft_model is not None else None,
                    max_chunk_size=gcs,
                    cpu_cache_size=int(ccs * 1024 ** 3),
                    recurrent_cache_size=int(rcs * 1024 ** 3))
    TOK = tokenizer
    # Without stop conditions generation never ends at <|im_end|> and always runs to max_tokens
    # (upstream bug: every reply was exactly max_tokens long).
    global STOP
    STOP = [e for e in (getattr(config, "eos_token_id_list", None) or []) if e is not None]
    try:
        im_end = tokenizer.single_id("<|im_end|>")
        if im_end is not None and im_end not in STOP:
            STOP.append(im_end)
    except Exception:
        pass
    STOP.append("<|im_end|>")
    print("[api] stop conditions: %r" % (STOP,), flush=True)
    print("[api] generator ready (cpu tier: %s)"
          % (getattr(GEN, "cpu_page_cache", None) is not None), flush=True)


_TPL = None


def _load_template():
    """The model's own chat template, rendered the way HF transformers does it."""
    global _TPL
    if _TPL is not None or jinja2 is None:
        return _TPL
    src = None
    tpl_path = os.path.join(MODEL_DIR, "chat_template.jinja")
    if os.path.exists(tpl_path):
        src = open(tpl_path, encoding="utf-8").read()
    else:
        cfg = os.path.join(MODEL_DIR, "tokenizer_config.json")
        if os.path.exists(cfg):
            src = json.load(open(cfg, encoding="utf-8")).get("chat_template")
    if not isinstance(src, str):
        return None
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
                                        extensions=["jinja2.ext.loopcontrols"])

    def raise_exception(msg):
        raise jinja2.exceptions.TemplateError(msg)

    env.globals["raise_exception"] = raise_exception
    env.filters.setdefault("tojson", lambda x, **k: json.dumps(x, ensure_ascii=False, **k))
    _TPL = env.from_string(src)
    return _TPL


def render_chat(messages, tools=None, enable_thinking=True):
    """Prefer the model's own chat template; fall back to Qwen-style ChatML."""
    tpl = None
    try:
        tpl = _load_template()
    except Exception as exc:
        print("[api] chat template load failed (%r)" % exc, flush=True)
    if tpl is not None:
        try:
            kw = dict(messages=messages, add_generation_prompt=True,
                      enable_thinking=enable_thinking)
            if tools:
                kw["tools"] = tools
            out = tpl.render(**kw)
            if isinstance(out, str) and out.strip():
                return out, "jinja"
        except Exception as exc:
            print("[api] chat template failed (%r); falling back to ChatML" % exc, flush=True)
    parts = []
    for m in messages:
        parts.append("<|im_start|>%s\n%s<|im_end|>\n"
                     % (m.get("role", "user"), m.get("content", "") or ""))
    parts.append("<|im_start|>assistant\n")
    if not enable_thinking:
        parts.append("<think>\n\n</think>\n\n")
    return "".join(parts), "chatml"


def generate(prompt, max_tokens, temperature=None, top_p=None, top_k=None, min_p=None):
    """One completion. Fixes vs upstream, all of which produced garbage or runaway output:

    - encode_special_tokens=True: otherwise <|im_start|>/<|im_end|> are tokenised as plain
      text and the model never sees a real chat template
    - add_bos=False: Qwen has no BOS in its template
    - stop_conditions: otherwise every reply runs to max_tokens
    - a real ComboSampler: generate() swallows unknown kwargs, so temperature/top_p were ignored
    - completion_only=True and use the returned completion string: last_results["text"] is
      only the LAST streamed chunk (often a single character)
    """
    sampler = ComboSampler(
        temperature=0.6 if temperature is None else float(temperature),
        top_p=0.95 if top_p is None else float(top_p),
        top_k=20 if top_k is None else int(top_k),
        min_p=0.0 if min_p is None else float(min_p),
    )
    with LOCK:
        t0 = time.time()
        text, last = GEN.generate(prompt, max_new_tokens=max_tokens, sampler=sampler,
                                  stop_conditions=STOP, add_bos=False,
                                  encode_special_tokens=True, completion_only=True,
                                  return_last_results=True)
        gen_s = time.time() - t0
    r = dict(last) if isinstance(last, dict) else {}
    r["text"] = text or ""
    r["gen_seconds"] = gen_s
    return r


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "collabosm"

    def log_message(self, fmt, *a):
        print("[api] " + (fmt % a), flush=True)

    def _send(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "authorization,content-type")
        self.send_header("Access-Control-Allow-Methods", "POST,GET,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorized(self):
        if not API_KEY:
            return True
        got = self.headers.get("Authorization", "")
        if got == "Bearer " + API_KEY or self.headers.get("x-api-key", "") == API_KEY:
            return True
        self._send(401, {"error": {"message": "invalid or missing API key"}})
        return False

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._send(200, "ok", "text/plain")
        if not self._authorized():
            return
        if self.path.startswith("/v1/models"):
            return self._send(200, {"object": "list", "data": [
                {"id": "qwen3.8-flash-next-exl3", "object": "model",
                 "owned_by": "collabosm"}]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/v1/chat/completions"):
            return self._send(404, {"error": "not found"})
        # read the body BEFORE any early reply, or keep-alive parses it as the next request
        try:
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n)
        except Exception as exc:
            return self._send(400, {"error": {"message": "bad request: %r" % exc}})
        if not self._authorized():
            return
        try:
            req = json.loads(raw or b"{}")
        except Exception as exc:
            return self._send(400, {"error": {"message": "bad json: %r" % exc}})

        msgs = req.get("messages") or []
        ctk = req.get("chat_template_kwargs") or {}
        thinking = ctk.get("enable_thinking", req.get("enable_thinking", True))
        prompt, how = render_chat(msgs, tools=req.get("tools"), enable_thinking=bool(thinking))
        max_tokens = int(req.get("max_tokens") or req.get("max_completion_tokens") or 4096)
        stream = bool(req.get("stream"))

        try:
            r = generate(prompt, max_tokens, req.get("temperature"), req.get("top_p"),
                         req.get("top_k"), req.get("min_p"))
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {"error": {"message": repr(exc)}})

        text = r.get("text") or ""
        pt = r.get("prompt_tokens") or 0
        ct = r.get("cached_tokens") or 0
        nt = r.get("new_tokens") or 0
        hit = (100.0 * ct / pt) if pt else 0.0
        reason = r.get("eos_reason") or "stop"
        finish = "length" if reason == "max_new_tokens" else "stop"
        gs = r.get("gen_seconds") or 0.0
        print("[api] template=%s prompt=%d cached=%d (%.1f%% hit) new=%d end=%s %.2fs (%.1f tok/s)"
              % (how, pt, ct, hit, nt, reason, gs, nt / gs if gs else 0), flush=True)

        cid = "chatcmpl-%d" % int(time.time() * 1000)
        if not stream:
            return self._send(200, {
                "id": cid, "object": "chat.completion", "created": int(time.time()),
                "model": req.get("model", "qwen3.8-flash-next-exl3"),
                "choices": [{"index": 0, "finish_reason": finish,
                             "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": pt, "completion_tokens": nt,
                          "total_tokens": pt + nt, "cached_tokens": ct,
                          "gen_seconds": round(r.get("gen_seconds", 0.0), 3)}})

        # Streaming: this build has no incremental callback here, so the whole
        # completion is delivered as one delta followed by [DONE]. Clients that
        # only need SSE framing are happy; there is no token-by-token typing.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        for chunk in (
            {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
             "model": req.get("model", "qwen3.8-flash-next-exl3"),
             "choices": [{"index": 0, "delta": {"role": "assistant", "content": text},
                          "finish_reason": None}]},
            {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
             "model": req.get("model", "qwen3.8-flash-next-exl3"),
             "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ):
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8090)))
    a = ap.parse_args()
    build_engine()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("[api] listening on http://%s:%d" % (a.host, a.port), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()