#!/usr/bin/env python3
"""Opens the web build in headless Chromium as a phone and plays it with touch.

    python tests/web_smoke.py build/web/site build/web/smoke

Serves the folder locally, loads it at 390x844 with touch emulation, waits for frames, screenshots
the level 1 card and play, drags a slingshot shot with real touch events, and checks that a preference
file written in the page survives a reload. Fails on a JS error, a blank canvas, a page that scrolled
or zoomed, or a drag that changed nothing. YY_CHROMIUM names a browser to use instead of Playwright's.
"""
import functools, http.server, io, json, os, sys, threading
from playwright.sync_api import sync_playwright
from PIL import Image, ImageChops

WIDTH, HEIGHT = 390, 844
BROWSER_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
# Where the slingshot is pulled from and to, in CSS pixels on the level 1 board once the card is dismissed (the hole in the fog).
DRAG_FROM, DRAG_TO = (212, 672), (232, 712)

def serve(folder):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    handler = functools.partial(Quiet, directory=folder)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

def distinct_colours(image):
    return len(image.convert("RGB").resize((96, 208)).getcolors(1 << 20) or [])

def changed(a, b):
    box = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox()
    return box is not None

def main():
    folder, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    server = serve(folder)
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    problems, report = [], {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=BROWSER_ARGS, executable_path=os.environ.get("YY_CHROMIUM") or None)
        context = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2, has_touch=True, is_mobile=True)
        page = context.new_page()
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.on("console", lambda m: m.type == "error" and problems.append(f"console error: {m.text}"))
        cdp = context.new_cdp_session(page)

        def touch(kind, x=None, y=None):
            points = [] if x is None else [{"x": x, "y": y, "id": 1}]
            cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

        def shot(name):
            data = page.screenshot(path=os.path.join(out, name + ".png"))
            return Image.open(io.BytesIO(data))

        def frames():
            return page.evaluate("Module._yy_web_frames()")

        page.goto(url)
        page.wait_for_function("window.Module && Module._yy_web_frames && Module._yy_web_frames() > 30", timeout=120000)
        page.wait_for_timeout(500)
        card = shot("card")
        report["frames_after_load"] = frames()
        if distinct_colours(card) < 8: problems.append("blank canvas: the level 1 card has almost no colours")

        touch("touchStart", 195, 422); touch("touchEnd")             # the first press only dismisses the card
        page.wait_for_timeout(500)
        play = shot("play")
        if not changed(card, play): problems.append("tapping the card changed nothing")

        touch("touchStart", *DRAG_FROM)
        for i in range(1, 9):
            touch("touchMove", DRAG_FROM[0] + (DRAG_TO[0] - DRAG_FROM[0]) * i / 8, DRAG_FROM[1] + (DRAG_TO[1] - DRAG_FROM[1]) * i / 8)
            page.wait_for_timeout(30)
        page.wait_for_timeout(200)
        aim = shot("aim")
        if not changed(play, aim): problems.append("dragging placed no ball and drew no aim")
        touch("touchEnd")
        page.wait_for_timeout(150)
        fired = shot("fired")
        if not changed(aim, fired): problems.append("releasing the drag fired nothing")
        page.wait_for_timeout(1500)
        report["frames_after_play"] = frames()
        if report["frames_after_play"] <= report["frames_after_load"]: problems.append("the frame loop stopped")

        scroll = page.evaluate("[window.scrollX, window.scrollY, window.visualViewport.scale]")
        if scroll != [0, 0, 1]: problems.append(f"the page scrolled or zoomed: {scroll}")

        # Preferences: a file in the preference folder survives a reload.
        page.evaluate("""() => {
          FS.mkdirTree('/libsdl/YYEngine/TapDemo');
          FS.writeFile('/libsdl/YYEngine/TapDemo/smoke.txt', 'kept');
          Module.yySync();
        }""")
        page.wait_for_timeout(1000)
        page.reload()
        page.wait_for_function("window.Module && Module._yy_web_frames && Module._yy_web_frames() > 5", timeout=120000)
        report["preference_after_reload"] = page.evaluate("FS.readFile('/libsdl/YYEngine/TapDemo/smoke.txt', {encoding: 'utf8'})")
        if report["preference_after_reload"] != "kept": problems.append("a preference file did not survive a reload")

        problems += [f"yyErrors: {e}" for e in page.evaluate("window.yyErrors")]
        browser.close()
    server.shutdown()
    report["problems"] = problems
    with open(os.path.join(out, "report.json"), "w") as f: json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
