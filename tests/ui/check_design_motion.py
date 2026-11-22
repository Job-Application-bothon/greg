"""Browser regression check for Greg decoration.
Run with: uv run --no-project --with playwright python tests/ui/check_design_motion.py
Requires Chrome and a running Greg server; set GREG_UI_URL to override localhost:8858.
"""

import os

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    page = b.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    base = os.environ.get("GREG_UI_URL", "http://127.0.0.1:8858").rstrip("/")
    page.goto(base + "/")
    page.wait_for_timeout(500)
    assert page.locator(".ufo-scene").count() == 4
    assert page.locator(".forecast-stage .ufo-scene").count() == 0
    assert page.request.get(base + "/static/art/chrome-ufo.svg").status == 200
    for w, h in [(1440, 900), (375, 812), (375, 667), (768, 1024)]:
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(250)
        scanner = page.locator(".ufo-scanner").bounding_box()
        title = page.locator("#choose-csv .source-title").bounding_box()
        assert scanner["height"] > 30, ("Scanner is clipped", w, h, scanner)
        assert scanner["y"] + scanner["height"] <= title["y"] - 12, (w, h, scanner, title)
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.locator("#choose-bank").click()
    assert page.locator(".demo-form").is_visible()
    stage = page.locator("#bank-entry")
    box = stage.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.75, box["y"] + box["height"] * 0.4)
    scene = stage.locator(".ufo-scene")
    assert scene.evaluate('(e)=>e.style.getPropertyValue("--ufo-tilt")') not in ["", "0deg"]
    fly = stage.locator(".ufo-flyby")
    fly.evaluate("(e)=>{const a=e.getAnimations()[0];a.pause();a.currentTime=42000*.88-27000;}")
    page.wait_for_timeout(80)
    assert float(fly.evaluate("(e)=>getComputedStyle(e).opacity")) > 0
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_timeout(100)
    assert fly.evaluate("(e)=>getComputedStyle(e).display") == "none"
    assert stage.locator(".ufo-near").evaluate("(e)=>getComputedStyle(e).animationName") == "none"
    # Expose the existing initialized stage without importing account data.
    page.locator(".forecast-stage").evaluate(
        "e=>{document.body.append(e);e.style='position:fixed;top:100px;left:50px;width:1000px;height:650px;z-index:20';}"
    )
    page.emulate_media(reduced_motion="no-preference")
    page.wait_for_timeout(200)
    canvas = page.locator(".forecast-halftone")
    assert canvas.evaluate("(c)=>getComputedStyle(c).maskImage") == "none"
    original = canvas.evaluate("(c)=>c.toDataURL()")
    page.mouse.move(850, 200)
    page.wait_for_timeout(100)
    assert original != canvas.evaluate("(c)=>c.toDataURL()"), "Dots must respond to cursor"
    page.mouse.move(10, 10)
    page.wait_for_timeout(1600)
    assert original == canvas.evaluate("(c)=>c.toDataURL()"), "Dots must return to grid"
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_timeout(100)
    original = canvas.evaluate("(c)=>c.toDataURL()")
    page.mouse.move(800, 250)
    page.wait_for_timeout(100)
    assert original == canvas.evaluate("(c)=>c.toDataURL()"), "Reduced motion dots must stay static"
    assert not errors, errors
    print(
        "PASS: UFO hover, cursor tilt, scanner bounds, flyby, reduced motion, "
        "halftone response and return, click-through, mobile bounds; no JS errors"
    )
    b.close()
