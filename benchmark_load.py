"""Benchmark: measure load time with vs without resource blocking."""
import asyncio, time
from playwright.async_api import async_playwright

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
URL = 'https://www.starrailawards.com/Vote2026/index.html'

async def measure(label: str, install_blocker: bool):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=CHROME)
        ctx = await browser.new_context(viewport={'width': 1280, 'height': 1600},
                                        locale='zh-CN')
        if install_blocker:
            async def _route(route):
                req = route.request
                rt = req.resource_type
                if rt in ("font", "media"):
                    await route.abort()
                    return
                if rt == "image" and "static.appoint.icu" in req.url:
                    await route.abort()
                    return
                await route.continue_()
            await ctx.route("**/*", _route)
        page = await ctx.new_page()

        bytes_seen = [0]
        page.on('response', lambda r: bytes_seen.__setitem__(0,
            bytes_seen[0] + (int(r.headers.get('content-length', 0)) if r.headers.get('content-length','').isdigit() else 0)))

        t0 = time.time()
        await page.goto(URL, wait_until='domcontentloaded', timeout=60_000)
        t_dom = time.time() - t0
        try:
            await page.wait_for_load_state('networkidle', timeout=30_000)
        except Exception:
            pass
        t_idle = time.time() - t0

        # locate sand-gold card to confirm the page is functional even without images
        try:
            card = page.locator('.character-card', has_text='砂金').first
            visible = await card.is_visible()
        except Exception:
            visible = False

        print(f'{label:25s}  dom-loaded={t_dom:5.2f}s  network-idle={t_idle:5.2f}s  '
              f'~bytes_downloaded={bytes_seen[0]/1024:>7.1f} KB  '
              f'card_locatable={visible}')
        await browser.close()


async def main():
    # warm-up run (cache priming for fairness)
    print('warming up ...')
    await measure('  warm-up (no blocker)', install_blocker=False)
    print()
    print('=== measurements (cold cache each) ===')
    for label, blk in [('without blocker', False), ('with blocker', True)]:
        await measure(label, install_blocker=blk)


asyncio.run(main())
