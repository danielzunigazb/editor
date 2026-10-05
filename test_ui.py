#!/usr/bin/env python3
"""The web UI in a real browser (Playwright + Chromium) against a real server and engine, with a scripted model: sign in, create a project, upload, chat with tool calls and
stills, the timeline and edit list, undo/redo, export with progress and download, the live-preview frame, hostile text (no script ever runs), a phone-sized screen, basic
accessibility. This Chromium cannot play H.264, so playback itself is NOT tested here (the page and the player's frame load; the video does not play).
Screenshots go to $UI_SHOTS (default /tmp/ui_shots) for a human to look at. Run: python3 test_ui.py"""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["MLT_LOG"] = "off"
os.environ.pop("ANTHROPIC_API_KEY", None)
from app.model import ScriptedModel  # noqa: E402
from app_testlib import TOKEN, Server  # noqa: E402

B = os.path.join(HERE, "media", "clip_b.mp4")
SHOTS = os.environ.get("UI_SHOTS", "/tmp/ui_shots")
CHROME = next((p for p in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",) if os.path.exists(p)), None)
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:400]}]" if not cond and detail else ""))


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright is None or CHROME is None:
    print("SKIP the UI tests: playwright or Chromium is not available here, so they were NOT run")
    sys.exit(0)

os.makedirs(SHOTS, exist_ok=True)


def until(page, js, timeout=30, what=""):
    """Wait until the arrow function `js` returns something truthy in the page. (page.wait_for_function evaluates a string, which the page's strict Content-Security-Policy
    rightly refuses; page.evaluate goes through the debugging protocol, which does not.)"""
    end = time.time() + timeout
    while time.time() < end:
        if page.evaluate(js):
            return
        time.sleep(0.15)
    raise AssertionError(f"timed out waiting for: {what or js}")

S = Server()
XSS = "<img src=x onerror=\"window.__pwned=1\"> <script>window.__pwned=2</script> **not bold**"
try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1360, "height": 860})
        page = ctx.new_page()
        problems = []
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type == "error" and "401" not in m.text and "415" not in m.text and "favicon" not in m.text else None)

        # ---------------------------------------------------------------------------------------------------------------- sign in
        page.goto(S.url("/"))
        page.wait_for_selector("#login[open]")
        page.fill("#token", "wrong"); page.click("#login-form button[type=submit]")
        until(page, "() => document.querySelector('#login-error').textContent.length > 0", 30)
        check("a wrong token is refused with a message and the dialog stays", page.is_visible("#login") and "token" in page.inner_text("#login-error").lower(), page.inner_text("#login-error"))
        page.fill("#token", TOKEN); page.click("#login-form button[type=submit]")
        page.wait_for_selector("#empty:not([hidden])")
        check("after signing in with no projects the page offers to create one", page.is_visible("#empty") and not page.is_visible("#login"))
        page.screenshot(path=f"{SHOTS}/01_empty.png")

        # ---------------------------------------------------------------------------------------------------------------- project and upload
        page.click("#empty-new"); page.fill("#p-name", "Demo <b>project</b>"); page.select_option("#p-format", "1280x720"); page.select_option("#p-fps", "25")
        page.click("#new-form button[type=submit]")
        page.wait_for_selector("#app:not([hidden])")
        check("a project is created and selected; its name is shown as text (no markup from the name)", page.input_value("#project-select") != "" and "<b>project</b>" in page.inner_text("#project-select"))
        check("the chat greets", "Hi!" in page.inner_text("#log"))
        page.click("#t-files")
        page.set_input_files("#file", B)
        until(page, "() => document.querySelector('#uploads').textContent.includes('imported as')", 60)
        check("an uploaded video is imported and listed as a source", "clip_b" in page.inner_text("#sources") and "imported as clip_b" in page.inner_text("#uploads"), page.inner_text("#sources"))
        page.set_input_files("#file", {"name": "run.exe", "mimeType": "application/octet-stream", "buffer": b"MZ"})
        until(page, "() => document.querySelector('#uploads').textContent.includes('not a file type')", 20)
        check("a refused file shows the reason in red", "not a file type" in page.inner_text("#uploads"))
        page.screenshot(path=f"{SHOTS}/02_files.png")

        # ---------------------------------------------------------------------------------------------------------------- chat
        pid = page.input_value("#project-select")
        S.holder["by_project"][pid] = ScriptedModel([
            [{"type": "text", "text": "Cutting and titling. " + XSS}, {"type": "tool_use", "name": "add_clip", "input": {"source": "clip_b", "start_s": 0, "end_s": 3}}],
            [{"type": "tool_use", "name": "add_text", "input": {"text": "Hola " + XSS, "start_s": 0.5, "dur_s": 1.5}}],
            [{"type": "tool_use", "name": "get_still", "input": {"time_s": 1.0}}],
            [{"type": "text", "text": "All done."}],
        ], delay=0.02)
        page.click("#t-preview")
        page.fill("#input", "make a short intro"); page.press("#input", "Enter")
        until(page, "() => document.querySelector('#send').hidden === true", 30)
        check("while the model answers, Send is replaced by Stop and the box is locked", page.is_visible("#stop") and page.is_disabled("#input"))
        until(page, "() => document.querySelector('#send').hidden === false", 90)
        until(page, "() => document.querySelectorAll('#timeline .bar').length >= 2", 30)
        log = page.inner_text("#log")
        check("the user's message and the model's answer are in the chat", "make a short intro" in log and "All done." in log, log[:300])
        check("tool calls appear as collapsible boxes with their results (3 of them, none failed)", page.locator("details.tool").count() == 3 and page.locator("details.tool.bad").count() == 0 and page.locator("details.tool.run").count() == 0)
        check("the still the model asked for is shown as a thumbnail", page.locator(".thumbs img").count() == 1 and page.eval_on_selector(".thumbs img", "i => i.naturalWidth") > 50)
        check("hostile text from the model is shown literally and no script ever ran", "<img src=x onerror" in log and page.evaluate("window.__pwned") is None and page.locator("#log img[src='x']").count() == 0)
        check("the timeline draws the clip and the title", page.locator("#timeline .bar").count() >= 2 and "3.0 s" in page.inner_text("#duration"), page.inner_text("#duration"))
        check("the edit list shows the edits", page.locator("#edits li").count() >= 2, page.inner_text("#edits"))
        check("the model cost is shown", "Model cost: $" in page.inner_text("#cost"), page.inner_text("#cost"))
        page.click(".thumbs img"); page.wait_for_selector("#zoom[open]")
        check("clicking a thumbnail enlarges it", page.is_visible("#zoom-img")); page.keyboard.press("Escape")
        page.screenshot(path=f"{SHOTS}/03_chat.png")

        # ---------------------------------------------------------------------------------------------------------------- undo / redo
        n = page.locator("#edits li").count()
        page.click("#undo"); until(page, f"() => document.querySelectorAll('#edits li').length < {n}")
        check("undo removes the last edit from the timeline and the list", page.locator("#edits li").count() == n - 1)
        page.click("#redo"); until(page, f"() => document.querySelectorAll('#edits li').length === {n}")
        check("redo brings it back", page.locator("#edits li").count() == n)

        # ---------------------------------------------------------------------------------------------------------------- reload keeps the conversation
        page.reload(); page.wait_for_selector("#app:not([hidden])")
        until(page, "() => document.querySelectorAll('details.tool').length === 3", 30)
        check("after a reload the conversation, the tool boxes and the timeline are back", "make a short intro" in page.inner_text("#log") and page.locator("#timeline .bar").count() >= 2)

        # ---------------------------------------------------------------------------------------------------------------- live preview frame
        page.click("#open-viewer"); page.wait_for_selector("#viewer:not([hidden])")
        until(page, "() => !!(document.querySelector('#viewer').contentDocument && document.querySelector('#viewer').contentDocument.querySelector('video'))", 30)
        check("the live preview frame loads the player through the proxy (it cannot play H.264 in this Chromium)", page.is_visible("#viewer-tab"))
        frame = next(f for f in page.frames if f != page.main_frame)
        until(page, "() => !!document.querySelector('#viewer').contentDocument.querySelector('#edits, .ops, svg, ol, ul, table')", 20)
        check("the player page (same origin as the app) shows the hostile edit text as text: no script ran in it or in the app",
              frame.evaluate("window.__pwned") is None and page.evaluate("window.__pwned") is None and frame.locator("img[src='x']").count() == 0, frame.content()[:200])

        # ---------------------------------------------------------------------------------------------------------------- export
        page.click("#t-export"); page.select_option("#quality", "draft"); page.click("#export")
        until(page, "() => !!(document.querySelector('#export-status progress') || document.querySelector('#export-status a'))", 30)
        page.wait_for_selector("#export-status a", timeout=120000)
        href = page.get_attribute("#export-status a", "href")
        r = ctx.request.get(S.url(href))
        check("the export finishes and the Download link serves an mp4", r.status == 200 and r.body()[4:8] == b"ftyp" and len(r.body()) > 3000, (r.status, len(r.body())))
        check("the export appears in the list", page.locator("#exports a").count() == 1)
        page.screenshot(path=f"{SHOTS}/04_export.png")

        # ---------------------------------------------------------------------------------------------------------------- theme, phone, accessibility
        page.click("#theme"); t1 = page.evaluate("document.documentElement.dataset.theme"); page.click("#theme"); t2 = page.evaluate("document.documentElement.dataset.theme")
        check("the theme button switches between light and dark", {t1, t2} == {"light", "dark"}, (t1, t2))
        page.click("#t-preview"); page.screenshot(path=f"{SHOTS}/05_preview_dark_or_light.png")
        unlabeled = page.evaluate("""() => [...document.querySelectorAll('input,select,textarea,button')].filter(e => !e.closest('[hidden]') && e.type !== 'hidden' &&
            !(e.labels && e.labels.length) && !e.getAttribute('aria-label') && !e.textContent.trim() && !e.title).map(e => e.id || e.tagName)""")
        check("every control has a visible or accessible name", unlabeled == [], unlabeled)
        check("the page declares its language and a title", page.evaluate("document.documentElement.lang") == "en" and page.title() != "")
        m = ctx.new_page(); m.set_viewport_size({"width": 390, "height": 844}); m.goto(S.url("/"))
        m.wait_for_selector("#app:not([hidden])")
        check("on a phone-sized screen nothing forces a sideways scroll", m.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), m.evaluate("[document.documentElement.scrollWidth, window.innerWidth]"))
        m.screenshot(path=f"{SHOTS}/06_phone.png", full_page=True)
        check("no script errors, CSP violations or failed requests were logged in the whole session", problems == [], problems[:5])
        csp = ctx.request.get(S.url("/")).headers.get("content-security-policy", "")
        check("the page is served with a strict Content-Security-Policy", "default-src 'self'" in csp and "object-src 'none'" in csp, csp)
        b.close()
finally:
    S.stop()
print(f"\n{ok} passed, {bad} failed   (screenshots in {SHOTS})")
sys.exit(1 if bad else 0)
