"""Read-only probe of the current /Vote2026/index.html page.

Goal:
  1. Confirm structure (whether new URL uses same .character-card/.vote-btn).
  2. Enumerate every .character-card in DOM order with its name + position.
  3. Find indices for 万敌 / 遐蝶 / 砂金.
  4. Also fetch the data array from character.js?v=3 for cross-check.
"""
import asyncio, json, os, re
from playwright.async_api import async_playwright
import httpx

URL    = 'https://www.starrailawards.com/Vote2026/index.html'
OUTDIR = r'd:\Arbeit\MessageForVote\extracted\page_probe_v2'
os.makedirs(OUTDIR, exist_ok=True)

TARGETS = ['万敌', '遐蝶', '砂金']

async def main():
    # 1. Live DOM probe
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
            viewport={'width': 1280, 'height': 1600},
            locale='zh-CN',
        )
        page = await ctx.new_page()
        print(f'navigating {URL}')
        await page.goto(URL, wait_until='domcontentloaded', timeout=30_000)
        try:
            await page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(3)

        # save HTML + screenshot
        html = await page.content()
        open(os.path.join(OUTDIR, 'rendered.html'), 'w', encoding='utf-8').write(html)
        await page.screenshot(path=os.path.join(OUTDIR, 'fullpage.png'),
                              full_page=True)
        print(f'saved rendered.html ({len(html)} chars) + fullpage.png')

        # enumerate all .character-card with full info
        cards = await page.evaluate("""
            () => {
                const cards = Array.from(document.querySelectorAll('.character-card'));
                return cards.map((c, i) => {
                    const img = c.querySelector('img');
                    const name = (c.querySelector('.character-name')?.innerText || '').trim();
                    const dataset = Object.assign({}, c.dataset);
                    return {
                        domIndex: i,
                        name,
                        imgSrc: img ? img.src : null,
                        dataset,
                        attrs: Array.from(c.attributes || []).reduce(
                            (o,a)=>{o[a.name]=a.value; return o;}, {}),
                    };
                });
            }
        """)
        json.dump(cards, open(os.path.join(OUTDIR, 'cards.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        print(f'\nDOM enumeration: {len(cards)} cards rendered\n')

        for tgt in TARGETS:
            hits = [c for c in cards if c['name'] == tgt]
            if hits:
                c = hits[0]
                key = c['imgSrc'].rsplit('/', 1)[-1].split('.')[0] if c['imgSrc'] else '?'
                print(f"  '{tgt}': DOM index = {c['domIndex']:3d}, "
                      f"img-key = '{key}', dataset = {c['dataset']}")
            else:
                print(f"  '{tgt}': NOT FOUND in current DOM")

        await browser.close()

    # 2. Also pull the raw character.js data array for cross-check
    print(f'\nfetching character.js for cross-check ...')
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get('https://static.appoint.icu/Railvote/character.js?v=3')
            r.raise_for_status()
            js = r.text
        open(os.path.join(OUTDIR, 'character.js'), 'w', encoding='utf-8').write(js)

        # parse the array — it's a const characterData = [{...}, {...}, ...]
        # extract id+name pairs robustly
        items = re.findall(r'\{\s*id\s*:\s*(\d+)\s*,\s*name\s*:\s*"([^"]+)"', js)
        print(f'\ncharacter.js array size: {len(items)}')
        # find target positions in the DATA ARRAY (which is what original .exe uses)
        for tgt in TARGETS:
            for arr_idx, (cid, cname) in enumerate(items):
                if cname == tgt:
                    print(f"  '{tgt}': data-array index = {arr_idx:3d}, "
                          f"character id = {cid}")
                    break
            else:
                print(f"  '{tgt}': NOT FOUND in data array")
    except Exception as e:
        print(f'character.js fetch failed: {e!r}')


if __name__ == '__main__':
    asyncio.run(main())
