"""Inspect ALL .custom-alert-button elements on page (visible + hidden)
so we can build a tighter selector that won't pick a hidden one.
"""
import asyncio, json, os
from playwright.async_api import async_playwright

OUT = r'd:\Arbeit\MessageForVote\extracted\confirm_button_probe'
os.makedirs(OUT, exist_ok=True)

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
URL = 'https://www.starrailawards.com/Vote2026/index.html'


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=CHROME)
        ctx = await browser.new_context(viewport={'width': 1280, 'height': 1600},
                                        locale='zh-CN')
        page = await ctx.new_page()
        await page.goto(URL, wait_until='domcontentloaded', timeout=60_000)
        try:
            await page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(2.5)

        # 1. Enumerate every .custom-alert-button (and ...-2 variant) on the page,
        #    visible or hidden. Show their parent overlays.
        info = await page.evaluate("""
            () => {
                const out = {};
                for (const sel of ['.custom-alert-button', '.custom-alert-button-2', '.custom-alert-overlay', '.custom-alert-overlay2', '.custom-alert-overlay3']) {
                    const list = Array.from(document.querySelectorAll(sel));
                    out[sel] = list.map(el => {
                        const cs = window.getComputedStyle(el);
                        // walk up to find which alert-overlay this is in
                        let p = el.parentElement;
                        let overlay_cls = null;
                        while (p) {
                            if (p.className && typeof p.className === 'string' && p.className.includes('alert-overlay')) {
                                overlay_cls = p.className;
                                break;
                            }
                            p = p.parentElement;
                        }
                        return {
                            cls: el.className,
                            text: (el.innerText || '').trim().slice(0, 60),
                            visible: cs.display !== 'none' && cs.visibility !== 'hidden',
                            inline_display: el.style.display,
                            parent_overlay: overlay_cls,
                        };
                    });
                }
                return out;
            }
        """)
        print('=== All .custom-alert-* elements on page ===\n')
        for sel, items in info.items():
            print(f'\n{sel}: {len(items)} occurrences')
            for it in items:
                print(f'  visible={it["visible"]!s:>5s}  inline_display={it["inline_display"]!r:>10s}  '
                      f'text={it["text"]!r:<25s}  parent_overlay={it["parent_overlay"]}')

        json.dump(info, open(os.path.join(OUT, 'buttons.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        print(f'\nFull dump → {OUT}\\buttons.json')
        await browser.close()


asyncio.run(main())
