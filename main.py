#!/usr/bin/env python3
"""proxypool collector — registers GoLogin trial accounts and stores their proxies.

Usage (from the repo/scripts directory, or set BASE_DIR in .env):
  python main.py 5            -> register 5 accounts
  python main.py --burst 6    -> run continuously with 6 parallel workers (daemon)
  python main.py --stop       -> stop burst mode and all workers
  python main.py --dedupe     -> deduplicate the collected proxy list

Registration flow (browser-driven, captcha solves itself):
  open sign-up page through an upstream proxy -> fill email -> tick the ToS
  checkbox -> "Try 7 days for Free" -> fill both password fields ->
  "Create an account" -> wait for the account POST (201 + token) ->
  GET the proxy list with that token.

The captcha widget is invisible and passes automatically in-page; the token is
origin-bound and single-use, which is why registration must happen through the
real UI rather than plain HTTP calls.
"""
import argparse, asyncio, json, os, secrets, signal, string, subprocess, sys, time, urllib.request
from pathlib import Path

# ---- configuration (all overridable via .env / environment) ----------------
BASE = Path(os.environ.get("BASE_DIR") or Path(__file__).resolve().parent)
POOL = Path(os.environ.get("POOL_FILE") or BASE / "proxies_http.txt")
EMAIL_DOMAIN = os.environ.get("EMAIL_DOMAIN", "")
SIGNUP = os.environ.get("SIGNUP_URL", "https://app.gologin.com/sign_up_frame")
PROXY_API = os.environ.get("PROXY_API_URL", "https://api.gologin.com/proxy")
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "4"))
DISPLAY = os.environ.get("DISPLAY", ":99")

OUT_LOG = BASE / "accounts.jsonl"
OUT_PX = BASE / "proxies_collected.txt"
OUT_DEDUPE = BASE / "gologin_proxies.txt"
STOP_FILE = BASE / "STOP"

if not EMAIL_DOMAIN:
    sys.exit("EMAIL_DOMAIN is not set — copy .env.example to .env and configure it.")
if not POOL.exists():
    sys.exit(f"proxy pool file not found: {POOL} — set POOL_FILE in .env (host:port:user:pass per line).")

POOL_LINES = [l.strip() for l in POOL.read_text().splitlines() if l.strip()]
_rot = [0]


def next_proxy():
    p = POOL_LINES[_rot[0] % len(POOL_LINES)]
    _rot[0] += 1
    return p.split(":")


def make_email():
    return "gl" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10)) + "@" + EMAIL_DOMAIN


def fetch_proxies(token):
    req = urllib.request.Request(PROXY_API)
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        return [f"{p['host']}:{p['port']}:{p['username']}:{p['password']}" for p in d.get("proxies", [])]
    except Exception:
        return []


def save(em, pw_, acc, lines):
    with open(OUT_LOG, "a") as f:
        f.write(json.dumps({"email": em, "password": pw_, "account_id": acc, "ts": time.time()}) + "\n")
    with open(OUT_PX, "a") as f:
        for l in lines:
            f.write(l + "\n")


async def one_attempt(browser, em, pw_):
    """One registration: email -> ToS checkbox -> 'Try 7 days for Free' ->
    both password fields -> 'Create an account' -> wait for POST /user."""
    ip, pt, us, pw = next_proxy()
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        proxy={"server": f"http://{ip}:{pt}", "username": us, "password": pw})
    try:
        page = await ctx.new_page()
        cap = {"status": None, "body": None}
        page.on("response", lambda rs: asyncio.create_task(_grab(rs, cap)))
        await page.goto(SIGNUP, wait_until="domcontentloaded", timeout=20000)
        email = page.get_by_placeholder("Your email")
        await email.wait_for(timeout=15000)
        await page.wait_for_timeout(1000)
        await email.fill(em)
        await page.locator("input[type=checkbox]").first.click(force=True)
        await page.get_by_role("button", name="Try 7 days for Free").click()
        await page.wait_for_selector("input[type=password]", timeout=15000)
        await page.wait_for_timeout(800)
        pws = page.locator("input[type=password]")
        await pws.nth(0).fill(pw_)
        await pws.nth(1).fill(pw_)
        await page.get_by_role("button", name="Create an account").click()
        for _ in range(40):                 # captcha is invisible; resolves in ~3-10s
            if cap["status"]:
                break
            await page.wait_for_timeout(500)
        return cap["status"], cap["body"]
    finally:
        try:
            await ctx.close()
        except Exception:
            pass


async def _grab(rs, cap):
    if rs.request.method == "POST" and "/user?" in rs.url:
        cap["status"] = rs.status
        try:
            cap["body"] = await rs.text()
        except Exception:
            pass


async def collect(n, max_att):
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=False, geoip=True) as browser:
        done = 0
        while done < n:
            em, pw_ = make_email(), secrets.token_urlsafe(14)
            t0 = time.time()
            for att in range(max_att):
                try:
                    status, body = await one_attempt(browser, em, pw_)
                except Exception as e:
                    print(f"[{em}] att{att} ERR {str(e)[:80]}", flush=True)
                    await asyncio.sleep(2)
                    continue
                if status not in (200, 201) or not body:
                    print(f"[{em}] att{att} {status or 'fail'} {str(body)[:60]}", flush=True)
                    await asyncio.sleep(6 if status == 429 else 2)
                    continue
                try:
                    reg = json.loads(body)
                except Exception:
                    print(f"[{em}] att{att} bad json", flush=True)
                    continue
                lines = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_proxies, reg.get("token") or reg.get("access_token"))
                save(em, pw_, reg.get("_id"), lines)
                done += 1
                print(f"OK {em} +{len(lines)}px ({time.time()-t0:.0f}s) total={done}/{n}", flush=True)
                break
            else:
                print(f"[{em}] GIVE UP after {max_att} attempts", flush=True)
            await asyncio.sleep(1)
        print(f"DONE {done}/{n}", flush=True)


def _procs():
    """PIDs of python processes running this script (match on file name)."""
    out = []
    me = os.path.basename(__file__)
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            raw = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\x00")
            argv = [a.decode(errors="replace") for a in raw if a]
        except Exception:
            continue
        if not argv:
            continue
        prog = os.path.basename(argv[0]).lower()
        if "python" not in prog:
            continue
        if any(a.endswith(me) for a in argv[1:]):
            out.append((int(pid), " ".join(argv)))
    return out


def burst(workers):
    """Daemonize (PPID=1) and loop batches until a STOP file appears."""
    if os.environ.get("_BURST_CHILD") != "1":
        if os.fork() > 0:      # parent exits
            return
        os.setsid()
        if os.fork() > 0:
            os._exit(0)
        os.environ["_BURST_CHILD"] = "1"
        devnull = os.open("/dev/null", os.O_RDWR)
        os.dup2(devnull, 0)
        log = os.open(str(BASE / "burst.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        os.dup2(log, 1); os.dup2(log, 2)
        print(f"[{time.time():.0f}] BURST START workers={workers}", flush=True)
        while True:
            if STOP_FILE.exists():
                print("STOP", flush=True); break
            print(f"[{time.time():.0f}] batch begin", flush=True)
            procs = []
            for _ in range(workers):
                procs.append(subprocess.Popen([sys.executable, __file__, "1"]))
                time.sleep(4)   # stagger
            for p in procs:
                p.wait()
            print(f"[{time.time():.0f}] batch done; accounts={sum(1 for _ in open(OUT_LOG))} px={sum(1 for _ in open(OUT_PX))}", flush=True)
            time.sleep(15)
        return
    print("burst child starting", flush=True)
    asyncio.run(collect(10**9, 5))


def stop():
    for pid, cl in _procs():
        try:
            os.kill(pid, signal.SIGTERM)
            print("term", pid, cl.strip()[:70], flush=True)
        except Exception as e:
            print("fail", pid, e, flush=True)
    try:
        STOP_FILE.touch()
    except Exception:
        pass
    try:
        for pid, cl in _procs():
            os.kill(pid, signal.SIGKILL)
            print("kill", pid, flush=True)
    except Exception:
        pass


def dedupe():
    seen = {}
    for l in OUT_PX.read_text().splitlines():
        if l.strip():
            seen[l.strip()] = 1
    OUT_DEDUPE.write_text("\n".join(seen) + "\n")
    print("unique:", len(seen))


def main():
    ap = argparse.ArgumentParser(description="GoLogin trial-account proxy collector")
    ap.add_argument("n", nargs="?", type=int, default=1, help="accounts to register (default 1)")
    ap.add_argument("--burst", type=int, metavar="W", help="run continuously with W workers (daemon)")
    ap.add_argument("--stop", action="store_true", help="stop burst mode and workers")
    ap.add_argument("--dedupe", action="store_true", help="deduplicate the proxy list")
    a = ap.parse_args()
    if a.stop:
        stop(); return
    if a.dedupe:
        dedupe(); return
    if a.burst is not None:
        burst(a.burst); return
    asyncio.run(collect(max(1, a.n), MAX_ATTEMPTS))


if __name__ == "__main__":
    os.environ.setdefault("DISPLAY", DISPLAY)
    main()
