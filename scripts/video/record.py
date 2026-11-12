"""Records the demo as a silent 1440x900 screen capture, each scene paced to its narration line.

    python scripts/video/record.py <base_url> <out_dir>      (out_dir must hold durations.json from tts.py)
"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

U, OUT = sys.argv[1].rstrip("/") + "/", Path(sys.argv[2])
plan = json.loads((OUT / "durations.json").read_text())
DUR = plan["durations"]
IDS = [l["id"] for l in plan["lines"]]
CUES, T0 = [], 0.0

CURSOR = """(() => { const d = document.createElement('div'); d.id='__cur';
 d.style.cssText='position:fixed;z-index:2147483647;width:22px;height:22px;border-radius:50%;background:rgba(90,78,110,.35);border:2px solid #5a4e6e;pointer-events:none;transform:translate(-50%,-50%);transition:width .15s,height .15s;left:-50px;top:-50px';
 document.addEventListener('mousemove', e => { d.style.left=e.clientX+'px'; d.style.top=e.clientY+'px'; }, true);
 document.addEventListener('mousedown', () => { d.style.width='34px'; d.style.height='34px'; }, true);
 document.addEventListener('mouseup', () => { d.style.width='22px'; d.style.height='22px'; }, true);
 const add=()=>document.body&&!document.getElementById('__cur')&&document.body.appendChild(d); add();
 new MutationObserver(add).observe(document.documentElement,{childList:true,subtree:true}); })();"""


class Scene:
    def __init__(self, sid):
        self.sid, self.start = sid, time.monotonic()

    async def __aenter__(self):
        CUES.append({"id": self.sid, "t": round(self.start - T0, 2)})
        print(f"{self.start - T0:6.1f}s  {self.sid}", flush=True)
        return self

    async def __aexit__(self, *exc):
        if exc[0]:
            return False
        left = DUR.get(self.sid, 0) + 0.7 - (time.monotonic() - self.start)
        if left > 0:
            await asyncio.sleep(left)


async def tap(pg, loc, pause=500):
    await loc.scroll_into_view_if_needed()
    box = await loc.bounding_box()
    await pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=16)
    await pg.wait_for_timeout(150)
    await loc.click()
    await pg.wait_for_timeout(pause)


async def glide(pg, loc, pause=800, block="center"):
    await loc.evaluate(f"e => e.scrollIntoView({{behavior: 'smooth', block: '{block}'}})")
    await pg.wait_for_timeout(pause)


async def fresh(pg):
    await pg.request.post(U + "api/demo/reset")
    await pg.evaluate("localStorage.clear()")
    await pg.goto(U)
    await pg.wait_for_timeout(700)


async def main():
    global T0
    t_ctx = 0.0
    async with async_playwright() as p:
        b = await p.chromium.launch()
        t_ctx = time.monotonic()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(OUT / "raw"),
                                  record_video_size={"width": 1440, "height": 900})
        await ctx.add_init_script(CURSOR)
        await ctx.add_init_script("try { localStorage.setItem('aw_tour_closed', '1') } catch (e) {}")
        pg = await ctx.new_page()
        pg.on("console", lambda m: print("CONSOLE", m.type, m.text[:300], flush=True) if m.type == "error" and "TUNNEL" not in m.text else None)
        pg.on("pageerror", lambda e: print("PAGEERR", str(e)[:400], flush=True))
        await pg.goto(U)
        await fresh(pg)
        try:   # warm the lazy AG Studio bundle so its scene doesn't open on spinners
            await pg.get_by_role("button", name="For businesses").click()
            await pg.get_by_role("button", name="Network insights").click()
            await pg.wait_for_selector(".studio-wrap canvas", timeout=30000)
            await pg.wait_for_timeout(1500)
            await pg.get_by_role("button", name="For families").click()
            await pg.wait_for_timeout(500)
        except Exception:
            pass
        T0 = time.monotonic()
        async with Scene("hook"):
            await pg.mouse.move(700, 300, steps=10)
        async with Scene("cert"):
            await tap(pg, pg.get_by_role("button", name="Fill in the demo family"), 500)
            await tap(pg, pg.get_by_role("button", name="Continue"), 700)
            await tap(pg, pg.get_by_role("button", name="Use the demo specimen"), 1500)
            await glide(pg, pg.locator(".step").nth(1), 1200, "start")
            fx = pg.locator("details.fx summary")
            if await fx.count():
                await tap(pg, fx.first, 1500)
        async with Scene("found"):
            await tap(pg, pg.get_by_role("button", name="Use the demo export"), 1500)
            await glide(pg, pg.locator(".step").nth(2), 2500, "start")
            await pg.mouse.wheel(0, 380)
        async with Scene("timeline"):
            await pg.wait_for_selector(".timeline .b-sch-event", timeout=15000)
            await glide(pg, pg.locator(".timeline"), 500)
        async with Scene("tell"):
            await tap(pg, pg.get_by_role("button", name=re.compile(r"^Tell \d+")), 2000)
            await glide(pg, pg.locator(".step").nth(3), 800, "start")
        async with Scene("email"):
            await tap(pg, pg.get_by_role("button", name="See the email"), 500)
        async with Scene("closed"):
            await pg.wait_for_selector("text=Receipt signed", timeout=30000)
            await glide(pg, pg.locator(".board"), 1500, "start")
        async with Scene("green"):
            await glide(pg, pg.locator(".timeline"), 300)
        async with Scene("business"):
            await pg.evaluate("scrollTo({top:0})")
            await tap(pg, pg.get_by_role("button", name="For businesses"), 1200)
            await tap(pg, pg.locator("nav.side button", has_text="The Daily Ledger"), 1200)
            await tap(pg, pg.locator(".ag-row").first, 1200)
            rv = pg.locator(".drawer .card", has_text="AI reviewer")
            if await rv.count():
                box = await rv.first.bounding_box()
                await pg.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + 30, steps=14)
                await pg.wait_for_timeout(3200)
            await pg.locator(".drawer").evaluate("e => e.scrollTo({top: 700, behavior: 'smooth'})")
            await pg.wait_for_timeout(1500)
            await tap(pg, pg.get_by_role("button", name=re.compile("Approve all")), 1200)
            await pg.keyboard.press("Escape")
        if "proof" in IDS:
            async with Scene("proof"):
                await tap(pg, pg.get_by_role("button", name="PayPal sandbox proof"), 1500)
                await pg.mouse.wheel(0, 420)
        async with Scene("insights"):
            await tap(pg, pg.get_by_role("button", name="Network insights"), 400)
            await pg.wait_for_selector(".studio-wrap canvas", timeout=20000)
            try:   # every widget drawn, none still "Loading..."
                await pg.wait_for_function("() => { const r = document.querySelector('.aw-receipt'); return r && /Total/.test(r.innerText) }", timeout=9000)
            except Exception:
                print("receipt not ready:", await pg.evaluate("(document.querySelector('.aw-receipt')||{}).innerText||'NO .aw-receipt'"), flush=True)
            await pg.wait_for_timeout(1500)
        async with Scene("lab"):
            await tap(pg, pg.get_by_role("button", name="Try to break it"), 700)
            await tap(pg, pg.get_by_role("button", name="Run every attack"), 2500)
            await pg.evaluate("scrollTo({top: 520, behavior: 'smooth'})")
        async with Scene("finale"):
            await tap(pg, pg.get_by_role("button", name="For families"), 300)
            await fresh(pg)
            await tap(pg, pg.get_by_role("button", name="Fill in the demo family"), 200)
            await tap(pg, pg.get_by_role("button", name="Continue"), 300)
            await tap(pg, pg.get_by_role("button", name="Use the demo specimen"), 600)
            await tap(pg, pg.get_by_role("button", name="Use the demo export"), 800)
            await tap(pg, pg.get_by_role("button", name=re.compile(r"^Tell \d+")), 1500)
            await tap(pg, pg.get_by_role("button", name="See the email"), 400)
            href = await pg.get_by_role("link", name=re.compile("Open it as")).get_attribute("href")
            await pg.goto(U.rstrip("/") + href)
            await pg.wait_for_timeout(1300)
            await tap(pg, pg.get_by_role("button", name=re.compile("I'm alive")), 2200)
            await pg.goto(U)
            await pg.wait_for_timeout(1200)
            await glide(pg, pg.locator(".step").nth(3), 1500, "start")
        CUES.append({"id": "_end", "t": round(time.monotonic() - T0, 2)})
        await ctx.close()
        await b.close()
    (OUT / "cues.json").write_text(json.dumps({"video_offset": round(T0 - t_ctx, 2), "cues": CUES}, indent=1))
    raw = sorted((OUT / "raw").glob("*.webm"), key=lambda f: f.stat().st_size)[-1]
    (OUT / "raw_path.txt").write_text(str(raw))
    print("recorded", raw)

asyncio.run(main())
