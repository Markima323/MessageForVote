"""Re-probe with the user's actual setup (Chrome, headful) to see why
.vote-btn click doesn't yield a modal in their environment.

Records every relevant DOM mutation in the 15 seconds after click,
so even slow captcha or modal show-up gets captured.
"""
import asyncio, json, os, time
from playwright.async_api import async_playwright

from _paths import EXTRACTED_DIR, ensure_dir, find_chrome

OUT = ensure_dir(os.path.join(EXTRACTED_DIR, 'probe_user_setup'))
CHROME = find_chrome()  # None → use Playwright bundled chromium
URL    = 'https://www.starrailawards.com/Vote2026/index.html'
TARGET = '砂金'

async def main():
    async with async_playwright() as pw:
        kwargs = {"headless": False}
        if CHROME:
            kwargs["executable_path"] = CHROME
        browser = await pw.chromium.launch(**kwargs)
        ctx = await browser.new_context(
            viewport={'width': 1280, 'height': 1600},
            locale='zh-CN',
        )
        page = await ctx.new_page()

        timeline = []
        def evt(label, **kw):
            timeline.append({'t': time.time(), 'label': label, **kw})

        page.on('console', lambda m: evt('console', type=m.type, text=m.text[:200]))
        page.on('request', lambda r: evt('request', method=r.method, url=r.url[:200], rt=r.resource_type)
                if any(s in r.url for s in ('captcha','/api/','vote','login','user'))
                else None)
        page.on('response', lambda r: evt('response', status=r.status, url=r.url[:200])
                if any(s in r.url for s in ('captcha','/api/','vote','login','user'))
                else None)
        page.on('framenavigated', lambda f: evt('framenavigated', url=f.url[:200]))
        page.on('dialog', lambda d: (evt('dialog', type=d.type, msg=d.message), asyncio.create_task(d.dismiss())))

        evt('start_navigate')
        await page.goto(URL, wait_until='domcontentloaded', timeout=60_000)
        try:
            await page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(3)
        evt('page_settled')

        # find sand-gold + record its DOM index
        cards = await page.evaluate("""
            () => Array.from(document.querySelectorAll('.character-card .character-name')).map(e => e.innerText.trim())
        """)
        print(f'40 cards rendered (total={len(cards)}); 砂金 at index = {cards.index(TARGET) if TARGET in cards else -1}')

        card = page.locator('.character-card', has_text=TARGET).first
        await card.scroll_into_view_if_needed()
        await page.screenshot(path=os.path.join(OUT, 'before_click.png'), full_page=False)
        evt('about_to_click')
        # Print what .vote-btn looks like in this card
        btn_info = await card.evaluate("""
            el => {
                const btn = el.querySelector('.vote-btn');
                if (!btn) return {found: false};
                const cs = window.getComputedStyle(btn);
                const r = btn.getBoundingClientRect();
                return {
                    found: true,
                    text: btn.innerText.trim(),
                    visible: cs.display !== 'none' && cs.visibility !== 'hidden',
                    bbox: {x: r.x, y: r.y, w: r.width, h: r.height},
                    pointer: cs.pointerEvents,
                    cls: btn.className,
                };
            }
        """)
        print(f'  .vote-btn info: {btn_info}')
        evt('vote_btn_info', **btn_info)

        await card.locator('.vote-btn').click()
        evt('clicked')
        print('  clicked .vote-btn — observing for 15 s ...')

        # poll DOM every 0.5 s for 15 s, recording any change in overlay state
        last_snapshot = ''
        for tick in range(30):
            await asyncio.sleep(0.5)
            snap = await page.evaluate("""
                () => {
                    const out = {};
                    // Aliyun captcha containers
                    const ws = document.querySelector('.window-show');
                    if (ws) out.window_show_visible = (window.getComputedStyle(ws).display !== 'none');
                    // confirm modal
                    const cm = document.querySelector('.custom-alert-overlay2');
                    if (cm) out.confirm_modal_visible = (window.getComputedStyle(cm).display !== 'none');
                    // any toast/error message at body level
                    const toasts = Array.from(document.querySelectorAll('[class*="toast"], [class*="message"], [class*="tips"], [class*="hint"]'))
                        .filter(e => e.offsetParent !== null && e.innerText.trim().length > 0)
                        .map(e => ({cls: e.className, text: e.innerText.slice(0, 200)}));
                    out.toasts = toasts;
                    // any visible login prompt
                    const login_kw = ['登录','login','请先','未登录'];
                    const loginEls = Array.from(document.querySelectorAll('div, span, p'))
                        .filter(e => e.offsetParent !== null && login_kw.some(k => (e.innerText || '').includes(k)) && (e.innerText || '').length < 100)
                        .map(e => ({cls: e.className, text: e.innerText}));
                    out.login_hints = loginEls.slice(0, 5);
                    return out;
                }
            """)
            sig = json.dumps(snap, ensure_ascii=False)
            if sig != last_snapshot:
                last_snapshot = sig
                evt(f'dom_change_{tick}', **snap)
                print(f'  +{tick*0.5:4.1f}s: window_show={snap.get("window_show_visible")} '
                      f'confirm={snap.get("confirm_modal_visible")} '
                      f'toasts={len(snap.get("toasts",[]))} '
                      f'login_hints={len(snap.get("login_hints",[]))}')

        await page.screenshot(path=os.path.join(OUT, 'after_click_15s.png'), full_page=False)
        json.dump(timeline, open(os.path.join(OUT, 'timeline.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2, default=str)
        print(f'\nTimeline saved to {OUT}\\timeline.json ({len(timeline)} events)')
        await browser.close()

asyncio.run(main())
