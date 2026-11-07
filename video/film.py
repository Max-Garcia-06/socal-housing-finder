"""Film the 30-second demo: title card, live locator scenes, end card.

    (cd web && python3 -m http.server 8765 &)  ;  python3 video/film.py
Writes video/out/common-ground-demo.mp4 (1920x1080, 30 fps). Scenes are filmed from
the real page with timestamped JPEG screenshots, then encoded at a constant 30 fps.
"""

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
FR = OUT / "frames"
URL = "http://localhost:8765/index.html"
W, H, SCALE = 1440, 810, 4 / 3  # -> 1920x1080
ENC = ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30"]

CAPTION_CSS = """
#cap{position:fixed;left:440px;right:20px;bottom:22px;z-index:9999;display:flex;justify-content:center;pointer-events:none}
#cap div{background:rgba(31,30,29,.88);color:#FAF9F5;font:500 19px/1.35 Inter,sans-serif;padding:12px 20px;border-radius:14px;
  max-width:860px;text-align:center;opacity:0;transform:translateY(8px);transition:all .45s ease}
#cap div.on{opacity:1;transform:none}
#cap b{color:#F0A886;font-weight:600}
#badge{position:fixed;right:20px;top:58px;z-index:9999;background:#FAF9F5;border:1px solid #E3DED3;border-radius:999px;
  padding:6px 12px;font:600 12px Inter;color:#1F1E1D;display:flex;gap:6px;align-items:center;box-shadow:0 1px 4px rgba(0,0,0,.1)}
#badge i{width:10px;height:10px;border-radius:50%;background:#D97757;display:inline-block}
.leaflet-control-container .legend{transform:scale(.95);transform-origin:bottom left}
"""

CARD = """<html><head><link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>body{margin:0;width:1440px;height:810px;background:#F4F1EA;color:#1F1E1D;font-family:Inter;display:flex;align-items:center;justify-content:center}
.w{width:1100px}.k{font:600 15px Inter;letter-spacing:.14em;text-transform:uppercase;color:#B85C3E}
h1{font:600 76px/1.05 'Source Serif 4',Georgia,serif;margin:14px 0 18px;letter-spacing:-.02em}
p{font:400 26px/1.45 Inter;color:#4A4842;margin:0;max-width:980px}p b{color:#1F1E1D}
.row{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;margin-top:34px}
.c{background:#FAF9F5;border:1px solid #E3DED3;border-radius:18px;padding:20px 22px}
.c b{display:block;font:600 40px 'Source Serif 4',serif;color:#B85C3E}.c span{font-size:17px;color:#6B6A63}
.f{margin-top:30px;font-size:15px;color:#6B6A63}.mark{width:58px;height:58px}
</style></head><body><div class="w">BODY</div></body></html>"""

LOGO = '<svg class="mark" viewBox="0 0 32 32"><path d="M16 3 L28 13 V29 H4 V13 Z" fill="#D97757"/><rect x="13" y="18" width="6" height="11" fill="#F4F1EA"/><path d="M16 7v6M13 10h6" stroke="#F4F1EA" stroke-width="2"/></svg>'

TITLE = LOGO + """<div class="k">Claude Impact Lab · UCI · Lane 1 Find it</div><h1>Common Ground</h1>
<p>California's SB 4 lets churches build <b>100% affordable homes by right</b>. Common Ground shows nonprofit
developers <b>which Orange County church sites qualify</b>, what they'd cost, and how to fund them.</p>"""

END = LOGO + """<h1 style="font-size:60px">Find it. Clear it. Fund it.</h1>
<div class="row"><div class="c"><b>1,168</b><span>OC church parcels screened against SB 4</span></div>
<div class="c"><b>926</b><span>pass every test we can check, each with its source</span></div>
<div class="c"><b>~26,400</b><span>homes possible on half of each eligible lot (estimate)</span></div></div>
<div class="f">Cross-checked: 1,902 acres of church land vs. Terner Center's independent 1,907 · Built with Claude<br>
Public data: SCAG, FEMA, CAL FIRE, CalGEM, Caltrans, CalEnviroScreen, TCAC/HCD, HUD, RAND, Overture · Prototype, not legal advice<br>
github.com/Max-Garcia-06/socal-housing-finder</div>"""


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def encode_frames(name, frames, dur, fade_in=0.25, fade_out=0.25):
    """frames: list of (t, path). Variable-timing JPEGs -> constant 30 fps mp4 of length dur."""
    lst = FR / f"{name}.txt"
    with open(lst, "w") as f:
        for i, (t, p) in enumerate(frames):
            nxt = frames[i + 1][0] if i + 1 < len(frames) else dur
            f.write(f"file '{p}'\nduration {max(nxt - t, 0.001):.4f}\n")
        f.write(f"file '{frames[-1][1]}'\n")
    tmp = OUT / f"{name}.tmp.mp4"
    vf = f"fps=30,scale=1920:1080,format=yuv420p,trim=duration={dur},fade=in:st=0:d={fade_in},fade=out:st={dur - fade_out}:d={fade_out}"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", vf, *ENC, str(tmp)])
    tmp.rename(OUT / f"{name}.mp4")


def encode_still(name, img, dur):
    tmp = OUT / f"{name}.tmp.mp4"
    vf = f"scale=1920:1080,format=yuv420p,loop=-1:1,trim=duration={dur},fade=in:st=0:d=0.35,fade=out:st={dur - 0.35}:d=0.35"
    run(["ffmpeg", "-y", "-framerate", "30", "-i", str(img), "-vf", vf, *ENC, str(tmp)])
    tmp.rename(OUT / f"{name}.mp4")


async def film(page, name, dur, script):
    """Run `script` (async fn) while recording the page with a CDP screencast for dur seconds."""
    import base64
    d = FR / name
    d.mkdir(parents=True, exist_ok=True)
    cdp = await page.context.new_cdp_session(page)
    frames, t0 = [], [None]

    async def on_frame(ev):
        ts = ev["metadata"]["timestamp"]
        if t0[0] is None:
            t0[0] = ts
        p = d / f"{len(frames):05d}.jpg"
        p.write_bytes(base64.b64decode(ev["data"]))
        frames.append((ts - t0[0], p))
        try:
            await cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:
            pass

    cdp.on("Page.screencastFrame", lambda ev: asyncio.ensure_future(on_frame(ev)))
    await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080, "everyNthFrame": 1})
    # keep compositor producing frames even when idle
    await page.evaluate("window.__tick = setInterval(() => { document.body.style.outlineColor = 'rgba(0,0,0,' + (Math.random()*0.001) + ')'; }, 30); 0")
    start = time.monotonic()
    await script()
    while time.monotonic() - start < dur:
        await asyncio.sleep(0.05)
    await cdp.send("Page.stopScreencast")
    await page.evaluate("clearInterval(window.__tick); 0")
    await asyncio.sleep(0.2)
    await cdp.detach()
    frames = [f for f in frames if f[0] <= dur]
    print(f"{name}: {len(frames)} frames in {dur}s ({len(frames) / dur:.1f} fps)")
    encode_frames(name, frames, dur)


async def caption(page, html):
    await page.evaluate("""h => { const c = document.querySelector('#cap div'); c.classList.remove('on');
      setTimeout(() => { c.innerHTML = h; c.classList.add('on'); }, 250); }""", html)


async def main():
    shutil.rmtree(OUT, ignore_errors=True)
    FR.mkdir(parents=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome")
        ctx = await b.new_context(viewport={"width": W, "height": H}, device_scale_factor=SCALE)
        page = await ctx.new_page()

        for name, body in [("title", TITLE), ("end", END)]:
            await page.set_content(CARD.replace("BODY", body))
            await page.wait_for_timeout(1200)
            await page.screenshot(path=str(FR / f"{name}.jpg"), type="jpeg", quality=95)
        encode_still("s0_title", FR / "title.jpg", 3.2)

        await page.goto(URL)
        await page.evaluate("try{localStorage.clear()}catch(e){}; 0")
        await page.goto(URL)
        await page.add_style_tag(content=CAPTION_CSS)
        await page.evaluate("""() => { const c = document.createElement('div'); c.id = 'cap'; c.innerHTML = '<div></div>';
          document.body.appendChild(c); const b = document.createElement('div'); b.id = 'badge';
          b.innerHTML = '<i></i>Live demo · real public data'; document.body.appendChild(b); }""")
        await page.wait_for_timeout(3500)  # tiles

        async def s1():
            await caption(page, "<b>1,168</b> church parcels across Orange County, screened against SB 4")
            await page.wait_for_timeout(2300)
            await page.evaluate("document.getElementById('wells').click(); leafletMap.flyTo([33.70,-117.99], 13, {duration:1.2}); 0")
            await caption(page, "Oil wells, industry, flood and fire zones <b>rule out 242</b> automatically")
        await film(page, "s1_overview", 5.2, s1)

        async def s2():
            await page.evaluate("document.getElementById('wells').click(); leafletMap.flyTo([33.70,-117.83], 11, {duration:.6}); orgForm(); 0")
            await caption(page, "A nonprofit sets up its <b>profile</b>: who it houses, deal size, financing")
            await page.wait_for_timeout(500)
            await page.click("input[name=name]")
            await page.keyboard.type("Orange Coast Housing Partners", delay=22)
            for sel in ["input[name=prios][value=transit]", "input[name=cities][value='Santa Ana']",
                        "input[name=cities][value='Anaheim']", "input[name=cities][value='Garden Grove']"]:
                await page.check(sel)
                await page.wait_for_timeout(120)
            await page.wait_for_timeout(300)
            await page.click("#ofsave")
            await caption(page, "Sites re-rank for <b>that organization</b>, with the reasons why")
        await film(page, "s2_profile", 6.0, s2)

        # Warm the satellite tiles for the top match so the zoom-in is not blank on camera
        await page.evaluate("document.querySelector('.card').click(); 0")
        await page.wait_for_timeout(4000)
        await page.evaluate("document.getElementById('back').click(); leafletMap.setView([33.70,-117.83], 11, {animate:false}); 0")
        await page.wait_for_timeout(1500)

        async def s3():
            await page.evaluate("document.querySelector('.card').click(); 0")
            await caption(page, "Every SB 4 test, capacity, parking, and a <b>contact for the congregation</b>")
            await page.wait_for_timeout(2600)
            await page.evaluate("setTab('fund'); document.querySelector('#detail .scroll').scrollTo({top:470,behavior:'smooth'}); 0")
            await caption(page, "<b>Sourced cost</b> and a capital stack for a 9% and a 4% tax-credit deal")
        await film(page, "s3_site", 6.2, s3)

        async def s4():
            await page.evaluate("deepDive(+location.hash.split('=')[1]); 0")
            await caption(page, "One click: a <b>deep dive</b> with who to call, next steps, and Claude for research")
            await page.wait_for_timeout(1900)
            await page.evaluate("document.getElementById('dossier').scrollTo({top:520,behavior:'smooth'}); 0")
        await film(page, "s4_deepdive", 4.4, s4)

        await b.close()

    encode_still("s5_end", FR / "end.jpg", 4.6)
    parts = ["s0_title", "s1_overview", "s2_profile", "s3_site", "s4_deepdive", "s5_end"]
    (OUT / "list.txt").write_text("".join(f"file '{OUT / n}.mp4'\n" for n in parts))
    tmp = OUT / "demo.tmp.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(OUT / "list.txt"), "-c", "copy", "-movflags", "+faststart", str(tmp)])
    tmp.rename(OUT / "common-ground-demo.mp4")
    print("wrote", OUT / "common-ground-demo.mp4")


if __name__ == "__main__":
    asyncio.run(main())
