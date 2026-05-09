"""Diagnose why .vote-btn click yields no modal.

Navigates to the vote page, clicks 砂金's vote-btn, then waits and
records:
  - any new DOM elements (e.g. login prompt, error toast, alert overlay)
  - any URL change
  - any new console messages
  - any new network requests

No actual vote is submitted; we only observe the immediate page state
change after the click.
"""
import asyncio, json, os, time
from playwright.async_api import async_playwright

from _paths import EXTRACTED_DIR, ensure_dir

OUT = ensure_dir(os.path.join(EXTRACTED_DIR, 'click_response'))

URL = 'https://www.starrailawards.com/Vote2026/index.html'
TARGET = '砂金'

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
            viewport={'width': 1280, 'height': 1600},
            locale='zh-CN',
        )
        page = await ctx.new_page()

        console_msgs = []
        page.on('console', lambda m: console_msgs.append(
            {'type': m.type, 'text': m.text}))
        net_after = []
        clicked_at = [None]
        page.on('request', lambda r: (
            net_after.append({'method': r.method, 'url': r.url, 'rt': r.resource_type})
            if clicked_at[0] is not None else None))
        page.on('response', lambda r: None)
        dialog_msgs = []
        page.on('dialog', lambda d: (dialog_msgs.append(
            {'type': d.type, 'message': d.message}), asyncio.create_task(d.dismiss())))

        print(f'navigate {URL}')
        await page.goto(URL, wait_until='domcontentloaded', timeout=30_000)
        try:
            await page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(2.5)

        url_before = page.url
        await page.screenshot(path=os.path.join(OUT, 'before_click.png'),
                              full_page=False)
        # Snapshot the visible high-level structure BEFORE click.
        before_state = await page.evaluate("""
            () => {
                const overlays = Array.from(document.querySelectorAll('[class*="overlay"], [class*="modal"], [class*="dialog"], [class*="alert"], [class*="popup"]'));
                return overlays.map(e => ({
                    cls: e.className,
                    visible: e.offsetParent !== null,
                    text: (e.innerText || '').slice(0, 80),
                }));
            }
        """)

        # Locate 砂金 card and click its .vote-btn
        card = page.locator('.character-card', has_text=TARGET).first
        await card.scroll_into_view_if_needed()
        print('clicking .vote-btn')
        clicked_at[0] = time.time()
        await card.locator('.vote-btn').click()

        # Observe for 8 seconds
        for s in range(1, 9):
            await asyncio.sleep(1)
        url_after = page.url

        await page.screenshot(path=os.path.join(OUT, 'after_click.png'),
                              full_page=False)

        # Snapshot the visible high-level structure AFTER click.
        after_state = await page.evaluate("""
            () => {
                const overlays = Array.from(document.querySelectorAll('[class*="overlay"], [class*="modal"], [class*="dialog"], [class*="alert"], [class*="popup"]'));
                return overlays.map(e => ({
                    cls: e.className,
                    visible: e.offsetParent !== null,
                    visible_via_style: window.getComputedStyle(e).display !== 'none',
                    text: (e.innerText || '').slice(0, 200),
                    bbox: (function() { const r = e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; })(),
                }));
            }
        """)

        # Also dump all elements visible in viewport that might be overlays
        any_visible_overlay = await page.evaluate("""
            () => {
                const all = Array.from(document.querySelectorAll('body *'));
                const out = [];
                for (const el of all) {
                    const cs = window.getComputedStyle(el);
                    if (cs.position === 'fixed' && cs.display !== 'none' && cs.visibility !== 'hidden') {
                        const r = el.getBoundingClientRect();
                        if (r.width > 100 && r.height > 50) {
                            out.push({
                                tag: el.tagName.toLowerCase(),
                                cls: el.className,
                                z: cs.zIndex,
                                bbox: {x:r.x, y:r.y, w:r.width, h:r.height},
                                text: (el.innerText || '').slice(0, 200),
                            });
                        }
                    }
                }
                return out.slice(0, 20);
            }
        """)

        snap = {
            'url_before': url_before,
            'url_after': url_after,
            'url_changed': url_before != url_after,
            'console_messages': console_msgs[:30],
            'dialog_messages': dialog_msgs,
            'requests_after_click': net_after[:50],
            'before_overlays': before_state,
            'after_overlays': after_state,
            'fixed_position_overlays_after': any_visible_overlay,
        }
        json.dump(snap, open(os.path.join(OUT, 'analysis.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)

        print()
        print(f'URL before: {url_before}')
        print(f'URL after:  {url_after}')
        print(f'URL changed: {snap["url_changed"]}')
        print(f'Console messages: {len(console_msgs)}')
        for m in console_msgs[:10]:
            print(f'  {m["type"]}: {m["text"][:200]}')
        print(f'Dialog messages: {len(dialog_msgs)}')
        for d in dialog_msgs:
            print(f'  {d}')
        print(f'\nNew network requests after click: {len(net_after)}')
        for r in net_after[:15]:
            print(f'  {r["method"]} {r["rt"]:10s} {r["url"][:200]}')
        print(f'\nFixed-position large overlays AFTER click ({len(any_visible_overlay)}):')
        for o in any_visible_overlay:
            print(f'  {o["tag"]} cls=\"{o["cls"]}\" z={o["z"]} bbox={o["bbox"]}')
            print(f'    text: {o["text"][:180].strip()}')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
