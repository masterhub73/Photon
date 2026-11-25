"""Headless Chromium rendering (--render). Uses --dump-dom, no extra deps."""
import shutil
import subprocess
import sys
import tempfile

enabled = False
wait_ms = 8000
_browser = None

CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "chrome")
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
            ".webp", ".woff", ".woff2", ".ttf", ".pdf", ".zip", ".mp4",
            ".mp3", ".json", ".xml", ".txt")


def configure(render, wait=8000):
    """Enable rendering and verify the browser exists, failing early."""
    global enabled, wait_ms, _browser
    enabled, wait_ms = bool(render), int(wait)
    if not enabled:
        return
    for name in CANDIDATES:
        _browser = shutil.which(name)
        if _browser:
            return
    sys.exit("[!] --render requires Chromium, which was not found.\n"
             "    Termux: pkg install tur-repo && pkg install chromium\n"
             "    Then check it with: chromium --version")


def should_render(url):
    return not url.lower().split("?")[0].split("#")[0].endswith(SKIP_EXT)


def _run(flags, url, timeout, user_agent):
    with tempfile.TemporaryDirectory() as profile:
        cmd = [_browser] + flags + [
            "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
            "--no-first-run", f"--user-data-dir={profile}",
            f"--virtual-time-budget={wait_ms}", "--dump-dom"]
        if user_agent and user_agent != "Photon":
            cmd.append(f"--user-agent={user_agent}")
        cmd.append(url)
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)


def render(url, timeout=10, user_agent=None):
    """Return the rendered DOM as text, or '' on failure."""
    timeout = max(timeout, 30)
    try:
        out = _run(["--headless=new"], url, timeout, user_agent)
        if not out.stdout.strip():
            # older Chromium builds don't know --headless=new
            out = _run(["--headless"], url, timeout, user_agent)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return out.stdout
