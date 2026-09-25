#!/usr/bin/env python3
"""collabm - a small Claude-Code-style terminal chat for the collabosm endpoint.

Talk to the model in plain text instead of hand-writing JSON requests.

  python client/collabm.py                 # interactive
  python client/collabm.py -p "question"   # one-shot, prints the answer and exits
  echo "question" | python client/collabm.py

Endpoint: .local/endpoint.json (written by win/up.ps1), or env
COLLABM_BASE_URL + COLLABM_API_KEY.

Needs: requests, rich, prompt_toolkit (all already inside the google-colab-cli venv).

Input
  Enter            send
  Alt+Enter / Esc Enter   newline
  @path            attach a local text file inline (e.g. "review @src/main.py")
  /help            list commands
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
ENDPOINT_FILE = LOCAL / "endpoint.json"

DEFAULT_SYSTEM = (
    "You are a helpful, precise assistant. Answer in the language the user writes in. "
    "Use Markdown for structure and fenced code blocks for code. "
    "When files are attached they appear between <file path=...> tags."
)
MAX_ATTACH_BYTES = 200_000
THINK_RE = re.compile(r"<think>(.*?)</think>\s*", re.S)

console = Console(highlight=False)


# ----------------------------------------------------------------------------- endpoint
def load_endpoint():
    url = os.environ.get("COLLABM_BASE_URL")
    key = os.environ.get("COLLABM_API_KEY")
    model = os.environ.get("COLLABM_MODEL", "qwen3.8-flash-next-exl3")
    if not url and ENDPOINT_FILE.exists():
        d = json.loads(ENDPOINT_FILE.read_text(encoding="utf-8-sig"))
        url, key, model = d.get("base_url"), d.get("key"), d.get("model", model)
    if not url:
        console.print("[red]No endpoint.[/] Run [bold]win\\up.ps1[/] first "
                      "(or set COLLABM_BASE_URL / COLLABM_API_KEY).")
        sys.exit(2)
    return url.rstrip("/"), key or "", model


class Client:
    def __init__(self, base_url, key, model):
        self.base_url, self.key, self.model = base_url, key, model
        self.s = requests.Session()
        self.s.headers["Authorization"] = "Bearer " + key

    def health(self):
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        try:
            r = self.s.get(root + "/health", timeout=10)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def chat(self, messages, max_tokens, temperature, top_p):
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "top_p": top_p, "stream": False}
        r = self.s.post(self.base_url + "/chat/completions", json=body, timeout=900)
        if r.status_code == 524:
            raise RuntimeError("Cloudflare 524: the quick tunnel cuts responses after ~100 s. "
                               "Lower /max or ask for a shorter answer.")
        if r.status_code == 401:
            raise RuntimeError("401: API key rejected. Re-run win\\up.ps1 to refresh .local\\endpoint.json.")
        if r.status_code >= 400:
            raise RuntimeError("HTTP %d: %s" % (r.status_code, r.text[:500]))
        return r.json()


# ----------------------------------------------------------------------------- helpers
def expand_mentions(text, cwd):
    """Replace @path tokens with the file content. Returns (text, [attached paths])."""
    attached = []

    def repl(m):
        raw = m.group(1).strip("\"'")
        p = (cwd / raw).expanduser()
        if not p.is_file():
            return m.group(0)
        data = p.read_bytes()[:MAX_ATTACH_BYTES]
        body = data.decode("utf-8", errors="replace")
        attached.append(str(p))
        trunc = "" if p.stat().st_size <= MAX_ATTACH_BYTES else "\n[... truncated ...]"
        return '\n<file path="%s">\n%s%s\n</file>\n' % (raw, body, trunc)

    out = re.sub(r'(?<![\w@])@("[^"]+"|\'[^\']+\'|[^\s]+)', repl, text)
    return out, attached


def split_thinking(text):
    thoughts = "\n".join(t.strip() for t in THINK_RE.findall(text))
    answer = THINK_RE.sub("", text)
    # model may emit an unclosed <think> if it hit max_tokens mid-thought
    if "<think>" in answer:
        head, _, tail = answer.partition("<think>")
        thoughts = (thoughts + "\n" + tail).strip()
        answer = head
    return thoughts.strip(), answer.strip()


def run_with_spinner(fn, label="Thinking"):
    """Run fn() in a thread with a live timer. Ctrl+C abandons the wait."""
    box = {}

    def work():
        try:
            box["ok"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["err"] = exc

    th = threading.Thread(target=work, daemon=True)
    t0 = time.time()
    th.start()
    with console.status("", spinner="dots") as st:
        while th.is_alive():
            st.update("[cyan]%s[/] [dim]%.0fs  (Ctrl+C to stop waiting)[/]" % (label, time.time() - t0))
            th.join(0.2)
    if "err" in box:
        raise box["err"]
    return box["ok"], time.time() - t0


# ----------------------------------------------------------------------------- app
class App:
    def __init__(self, client, args):
        self.c = client
        self.system = args.system or DEFAULT_SYSTEM
        self.max_tokens = args.max_tokens
        self.temperature = args.temperature
        self.top_p = args.top_p
        self.show_think = args.show_thinking
        self.messages = []
        self.last_usage = {}
        self.total_new = 0
        # the workspace: @file / /run resolve here, conversations are saved here,
        # and COLLABM.md here is added to the system prompt (like CLAUDE.md)
        self.workdir = Path(args.workdir or Path.cwd()).resolve()
        self.cwd = self.workdir
        self.convdir = self.workdir / "conversations"
        self.convdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / ".collabm").mkdir(exist_ok=True)
        self.history_file = self.workdir / ".collabm" / "history.txt"
        self.memory_file = self.workdir / "COLLABM.md"
        self.new_conversation()

    def new_conversation(self):
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.transcript = self.convdir / (stamp + ".md")
        self.state_file = self.convdir / (stamp + ".json")

    def system_prompt(self):
        sp = self.system
        if self.memory_file.exists():
            mem = self.memory_file.read_text(encoding="utf-8", errors="replace").strip()
            if mem:
                sp += "\n\n<project_instructions file=\"COLLABM.md\">\n%s\n</project_instructions>" % mem
        return sp

    def save_state(self):
        self.state_file.write_text(json.dumps(
            {"model": self.c.model, "system": self.system, "messages": self.messages,
             "saved": dt.datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False, indent=1), encoding="utf-8")

    def list_conversations(self):
        return sorted(self.convdir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)

    def resume(self, path):
        d = json.loads(path.read_text(encoding="utf-8"))
        self.messages = d.get("messages", [])
        self.system = d.get("system", self.system)
        self.state_file = path
        self.transcript = path.with_suffix(".md")
        n = sum(1 for m in self.messages if m["role"] == "user")
        console.print("[dim]resumed %s (%d turns)[/]" % (path.name, n))
        for m in self.messages[-2:]:
            who = "you" if m["role"] == "user" else self.c.model
            body = split_thinking(m["content"])[1] if m["role"] == "assistant" else m["content"]
            console.print("[bold]%s[/]" % who)
            console.print(Markdown(body[:3000]))

    # -- conversation
    def payload(self):
        return [{"role": "system", "content": self.system_prompt()}] + self.messages

    def ask(self, user_text, quiet=False):
        text, attached = expand_mentions(user_text, self.cwd)
        for a in attached:
            console.print("[dim]  attached %s[/]" % a)
        self.messages.append({"role": "user", "content": text})
        try:
            r, secs = run_with_spinner(lambda: self.c.chat(self.payload(), self.max_tokens,
                                                           self.temperature, self.top_p))
        except KeyboardInterrupt:
            self.messages.pop()
            console.print("[yellow]stopped waiting[/] [dim](the server still finishes that request "
                          "before taking the next one)[/]")
            return None
        except Exception as exc:  # noqa: BLE001
            self.messages.pop()
            console.print("[red]error:[/] %s" % exc)
            return None
        raw = r["choices"][0]["message"]["content"] or ""
        self.messages.append({"role": "assistant", "content": raw})
        self.last_usage = r.get("usage", {})
        nt = self.last_usage.get("completion_tokens", 0)
        self.total_new += nt
        thoughts, answer = split_thinking(raw)
        if quiet:
            print(answer)
        else:
            self.render(thoughts, answer, secs, nt)
        self.log(user_text, raw)
        self.save_state()
        return answer

    def render(self, thoughts, answer, secs, nt):
        if thoughts:
            if self.show_think:
                console.print(Panel(Text(thoughts, style="dim italic"), title="thinking",
                                    title_align="left", border_style="dim"))
            else:
                console.print("[dim]  (thought for %d chars - /think to show)[/]" % len(thoughts))
        console.print(Markdown(answer or "_(empty)_", code_theme="monokai"))
        pt = self.last_usage.get("prompt_tokens", 0)
        console.print("[dim]  %d in / %d out · %.1fs · %.1f tok/s end-to-end[/]\n"
                      % (pt, nt, secs, nt / secs if secs else 0))

    def log(self, user, assistant):
        with self.transcript.open("a", encoding="utf-8") as fh:
            fh.write("## you\n\n%s\n\n## %s\n\n%s\n\n" % (user, self.c.model, assistant))

    # -- slash commands
    def command(self, line):
        cmd, _, arg = line[1:].partition(" ")
        arg = arg.strip()
        if cmd in ("exit", "quit", "q"):
            return "exit"
        if cmd in ("help", "h", "?"):
            console.print(Panel(HELP, title="collabm", border_style="cyan"))
        elif cmd == "clear":
            self.messages.clear()
            self.new_conversation()
            console.clear()
            console.print("[dim]new conversation -> %s[/]" % self.transcript.name)
        elif cmd == "resume":
            convs = self.list_conversations()
            if not convs:
                console.print("[dim]no saved conversations in %s[/]" % self.convdir)
            elif arg.isdigit() and 1 <= int(arg) <= len(convs):
                self.resume(convs[int(arg) - 1])
            else:
                for i, f in enumerate(convs[:15], 1):
                    try:
                        msgs = json.loads(f.read_text(encoding="utf-8")).get("messages", [])
                        first = next((m["content"] for m in msgs if m["role"] == "user"), "")
                    except Exception:
                        first = "?"
                    console.print(" [bold]%2d[/] %s  [dim]%s[/]" % (i, f.stem, first.replace("\n", " ")[:70]))
                console.print("[dim]/resume <n> to load one[/]")
        elif cmd == "memory":
            if arg:
                with self.memory_file.open("a", encoding="utf-8") as fh:
                    fh.write("- %s\n" % arg)
                console.print("[dim]added to %s[/]" % self.memory_file)
            else:
                txt = self.memory_file.read_text(encoding="utf-8") if self.memory_file.exists() else "(empty)"
                console.print(Panel(txt, title=str(self.memory_file), border_style="dim"))
        elif cmd == "system":
            if arg:
                self.system = arg
                console.print("[dim]system prompt set[/]")
            else:
                console.print(Panel(self.system, title="system prompt", border_style="dim"))
        elif cmd == "think":
            self.show_think = not self.show_think if not arg else arg in ("on", "1", "true")
            console.print("[dim]show thinking: %s[/]" % self.show_think)
        elif cmd == "temp":
            self.temperature = float(arg)
            console.print("[dim]temperature = %s[/]" % self.temperature)
        elif cmd == "max":
            self.max_tokens = int(arg)
            console.print("[dim]max_tokens = %d[/]" % self.max_tokens)
        elif cmd == "file":
            if not arg:
                console.print("usage: /file <path> [question]")
            else:
                path, _, q = arg.partition(" ")
                self.ask("@%s\n\n%s" % (path, q or "Read this file and summarise it."))
        elif cmd == "run":
            self.run_shell(arg)
        elif cmd == "retry":
            if len(self.messages) >= 2 and self.messages[-1]["role"] == "assistant":
                self.messages.pop()
                last = self.messages.pop()["content"]
                self.ask(last)
            else:
                console.print("[dim]nothing to retry[/]")
        elif cmd == "undo":
            if len(self.messages) >= 2:
                del self.messages[-2:]
                console.print("[dim]removed the last exchange[/]")
        elif cmd == "save":
            dest = Path(arg) if arg else self.transcript
            if arg:
                dest.write_text(self.transcript.read_text(encoding="utf-8")
                                if self.transcript.exists() else "", encoding="utf-8")
            console.print("[dim]transcript: %s[/]" % dest)
        elif cmd == "cd":
            p = (self.cwd / arg).resolve() if arg else Path.home()
            if p.is_dir():
                self.cwd = p
            console.print("[dim]cwd: %s[/]" % self.cwd)
        elif cmd == "status":
            ok = self.c.health()
            console.print("endpoint [bold]%s[/] · health %s · %d messages · %d tokens generated"
                          % (self.c.base_url, "[green]ok[/]" if ok else "[red]DOWN[/]",
                             len(self.messages), self.total_new))
            console.print("[dim]settings: max=%d temp=%s top_p=%s think=%s cwd=%s[/]"
                          % (self.max_tokens, self.temperature, self.top_p, self.show_think, self.cwd))
            console.print("[dim]workspace: %s · conversation: %s[/]" % (self.workdir, self.transcript.name))
        else:
            console.print("[red]unknown command[/] /%s  (try /help)" % cmd)
        return None

    def run_shell(self, cmdline):
        if not cmdline:
            console.print("usage: /run <command>   (output is shown, then offered to the model)")
            return
        shell = ["powershell", "-NoProfile", "-Command", cmdline] if os.name == "nt" else ["bash", "-lc", cmdline]
        try:
            p = subprocess.run(shell, cwd=self.cwd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300)
            out = (p.stdout + p.stderr).strip()
        except Exception as exc:  # noqa: BLE001
            out = "failed: %r" % exc
        console.print(Panel(out[-4000:] or "(no output)", title="$ " + cmdline, border_style="dim"))
        q = self.prompt_once("[ask the model about this output? type a question, or Enter to skip] ")
        if q and q.strip():
            self.ask("I ran `%s` in %s:\n\n```\n%s\n```\n\n%s" % (cmdline, self.cwd, out[-20000:], q))

    # -- input
    def prompt_once(self, msg):
        try:
            return self.session.prompt(msg) if self.session else input(msg)
        except (EOFError, KeyboardInterrupt):
            return ""

    def loop(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.completion import WordCompleter

        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        completer = WordCompleter(["/" + c for c in COMMANDS], sentence=True)
        self.session = PromptSession(history=FileHistory(str(self.history_file)), key_bindings=kb,
                                     completer=completer, complete_while_typing=True,
                                     bottom_toolbar=self.toolbar)
        self.banner()
        while True:
            try:
                line = self.session.prompt([("class:prompt", "› ")], multiline=False)
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if self.command(line) == "exit":
                    break
                continue
            self.ask(line)
        console.print("[dim]bye · conversation saved to %s[/]" % self.transcript)
        console.print("[yellow]The A100 is still billing until you run win\\down.ps1 (or /exit then collabm-down).[/]")

    def toolbar(self):
        u = self.last_usage
        ctx = (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0)) if u else 0
        return " %s · ctx %s tok · max %d · temp %s · /help " % (
            self.c.model, format(ctx, ","), self.max_tokens, self.temperature)

    def banner(self):
        ok = self.c.health()
        console.print(Panel.fit(
            "[bold]collabm[/]  ·  %s\n[dim]%s[/]\nstatus: %s\nworkspace: %s%s\n\n"
            "[dim]Enter send · Alt+Enter newline · @file attach · /help commands · Ctrl+D exit[/]"
            % (self.c.model, self.c.base_url, "[green]online[/]" if ok else "[red]not reachable[/] (run win\\up.ps1)",
               self.workdir, "  [dim](COLLABM.md loaded)[/]" if self.memory_file.exists() else ""),
            border_style="cyan"))


COMMANDS = ["help", "clear", "resume", "memory", "system", "think", "temp", "max", "file", "run", "retry",
            "undo", "save", "cd", "status", "exit"]
HELP = """[bold]/help[/]              this list
[bold]/clear[/]             start a new conversation (new file in conversations\\)
[bold]/resume[/] [n]        list saved conversations / load number n
[bold]/memory[/] [text]     show COLLABM.md (project instructions) or append a line to it
[bold]/system[/] [text]     show or set the system prompt
[bold]/think[/] [on|off]    show the model's <think> reasoning
[bold]/temp[/] 0.6          sampling temperature
[bold]/max[/] 4096          max new tokens per reply
[bold]/file[/] path [q]     attach a file and ask about it (or just write @path in a message)
[bold]/run[/] command       run a local command, then optionally ask the model about the output
[bold]/retry[/]             regenerate the last answer
[bold]/undo[/]              drop the last exchange
[bold]/cd[/] dir            change the directory used for @file and /run
[bold]/save[/] [path]       show / copy the transcript (auto-saved in <workspace>\\conversations)
[bold]/status[/]            endpoint health and session stats
[bold]/exit[/]              quit (Ctrl+D also works)"""


def main():
    ap = argparse.ArgumentParser(description="Chat with the collabosm endpoint")
    ap.add_argument("-p", "--prompt", help="one-shot: ask, print the answer, exit")
    ap.add_argument("--system", help="system prompt")
    ap.add_argument("--max-tokens", type=int, default=int(os.environ.get("COLLABM_MAX_TOKENS", 4096)))
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--show-thinking", action="store_true")
    ap.add_argument("--workdir", help="workspace folder (default: current directory)")
    ap.add_argument("-c", "--continue", dest="cont", action="store_true",
                    help="resume the most recent conversation in the workspace")
    args = ap.parse_args()

    app = App(Client(*load_endpoint()), args)
    app.session = None
    if args.cont:
        convs = app.list_conversations()
        if convs:
            app.resume(convs[0])
    if args.prompt:
        return 0 if app.ask(args.prompt, quiet=True) is not None else 1
    if not sys.stdin.isatty():
        text = sys.stdin.read().strip()
        return 0 if text and app.ask(text, quiet=True) is not None else 1
    app.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
