"""Headless Chromium rendering (--render) through the DevTools protocol.

Cross-origin iframes are separate CDP targets, so --dump-dom never sees them.
We launch Chromium with a debug port, wait for JS, then read the DOM of the
page and of every iframe target and return it all as one HTML string.
"""
import html
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

enabled = False
wait_ms = 20000
_browser = None
_lock = threading.Semaphore(1)  # one Chromium at a time (phone RAM)

CANDIDATES = ("chromium-browser", "chromium", "google-chrome", "chrome")
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
            ".webp", ".woff", ".woff2", ".ttf", ".pdf", ".zip", ".mp4",
            ".mp3", ".json", ".xml", ".txt")

# Page DOM plus any same-origin iframe documents reachable from it.
DOM_JS = """(function(){var s=document.documentElement.outerHTML;
document.querySelectorAll('iframe').forEach(function(f){try{
s+='\\n<!-- same-origin iframe -->\\n'+f.contentDocument.documentElement.outerHTML;
}catch(e){}});return s})()"""
TEXT_JS = "document.body ? document.body.innerText : ''"


def configure(render, wait=20000):
    """Enable rendering and verify requirements, failing early."""
    global enabled, wait_ms, _browser
    enabled, wait_ms = bool(render), int(wait)
    if not enabled:
        return
    try:
        import websocket  # noqa: F401
    except ImportError:
        sys.exit("[!] --render requires websocket-client.\n"
                 "    Install it with: pip install websocket-client")
    for name in CANDIDATES:
        _browser = shutil.which(name)
        if _browser:
            return
    sys.exit("[!] --render requires Chromium, which was not found.\n"
             "    Termux: pkg install x11-repo tur-repo && "
             "pkg install chromium\n"
             "    Then check it with: chromium-browser --version")


def should_render(url):
    return not url.lower().split("?")[0].split("#")[0].endswith(SKIP_EXT)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _targets(port):
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json", timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def _cdp_eval(ws_url, expr, timeout=15):
    import websocket
    try:
        ws = websocket.create_connection(
            ws_url, timeout=timeout, suppress_origin=True)
    except Exception:
        return ""
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr,
                                       "returnByValue": True}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                return msg.get("result", {}).get("result", {}).get(
                    "value", "") or ""
    except Exception:
        return ""
    finally:
        ws.close()


def _collect(port, proc):
    end = time.time() + 20
    while time.time() < end and proc.poll() is None:
        if _targets(port) is not None:
            break
        time.sleep(0.5)
    else:
        return ""
    time.sleep(wait_ms / 1000.0)  # real time: let JS and handshakes finish
    parts, texts = [], []
    for t in _targets(port) or []:
        if t.get("type") not in ("page", "iframe"):
            continue
        ws_url, turl = t.get("webSocketDebuggerUrl"), t.get("url", "")
        if not ws_url or turl.startswith(("about:", "devtools:", "chrome")):
            continue
        dom = _cdp_eval(ws_url, DOM_JS)
        if dom:
            label = turl.replace("--", "-")
            parts.append(f"<!-- photon-render {t['type']}: {label} -->\n{dom}")
        txt = _cdp_eval(ws_url, TEXT_JS)
        if txt:
            texts.append(txt)
    if not parts:
        return ""
    # innerText appended so plain-text emails/handles reach the regexes too
    return ("\n".join(parts) + "\n<pre data-photon-text>"
            + html.escape("\n".join(texts)) + "</pre>")


def render(url, timeout=10, user_agent=None):
    """Return page + iframe DOMs as one HTML string, or '' on failure."""
    with _lock, tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True) as profile:
        cmd = [_browser, "--headless=new", "--no-sandbox", "--disable-gpu",
               "--disable-dev-shm-usage", "--no-first-run",
               f"--user-data-dir={profile}",
               f"--remote-debugging-port={_free_port()}",
               "--remote-allow-origins=*"]
        port = int(cmd[-2].rsplit("=", 1)[1])
        if user_agent and user_agent != "Photon":
            cmd.append(f"--user-agent={user_agent}")
        cmd.append(url)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)
        try:
            return _collect(port, proc)
        except Exception:
            return ""
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
