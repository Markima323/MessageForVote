"""Read-only DOM probe for https://www.starrailawards.com.

Goal: find the CSS selectors needed to write the Playwright vote script.
We do NOT vote, we do NOT submit any form — only render and inspect.
"""
import asyncio
import json
import os
import sys
from playwright.async_api import async_playwright

OUT_DIR = r'd:\Arbeit\MessageForVote\extracted\page_probe'
os.makedirs(OUT_DIR, exist_ok=True)

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0 Safari/537.36",
            viewport={'width': 1280, 'height': 1600},
            locale='zh-CN',
        )
        page = await ctx.new_page()

        # Capture network for endpoint discovery
        api_calls = []
        page.on('request', lambda r: api_calls.append({
            'method': r.method, 'url': r.url, 'rt': r.resource_type
        }) if any(s in r.url for s in ('/api/', '/vote', '/captcha', '/submit', '/character')) else None)

        url = 'https://www.starrailawards.com'
        print(f'navigating to {url} ...')
        await page.goto(url, wait_until='domcontentloaded', timeout=30_000)

        # Allow Vue to hydrate
        try:
            await page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(3)

        # 1. Save full rendered HTML
        html = await page.content()
        with open(os.path.join(OUT_DIR, 'rendered.html'), 'w', encoding='utf-8') as f:
            f.write(html)
        print(f'saved rendered.html ({len(html)} chars)')

        # 2. Screenshot
        await page.screenshot(path=os.path.join(OUT_DIR, 'fullpage.png'),
                              full_page=True)
        print('saved fullpage.png')

        # 3. Search the DOM for "砂金"
        result = await page.evaluate("""
            () => {
                // find all elements whose visible text contains 砂金
                const all = Array.from(document.querySelectorAll('*'));
                const matches = [];
                for (const el of all) {
                    const t = (el.innerText || '').trim();
                    // only leaf-ish: short text, only this element
                    if (t === '砂金' || t.startsWith('砂金\\n') || t.includes('砂金')) {
                        // record selector chain
                        let chain = [];
                        let cur = el;
                        for (let i = 0; i < 8 && cur; i++) {
                            const cls = cur.className && typeof cur.className === 'string'
                                ? '.' + cur.className.trim().split(/\\s+/).join('.')
                                : '';
                            const id = cur.id ? '#' + cur.id : '';
                            chain.push(cur.tagName.toLowerCase() + id + cls);
                            cur = cur.parentElement;
                        }
                        matches.push({
                            tag: el.tagName.toLowerCase(),
                            id: el.id,
                            className: el.className,
                            text: t.slice(0, 60),
                            chain: chain,
                            outerHTMLSlice: el.outerHTML.slice(0, 400),
                            attrs: Array.from(el.attributes || []).reduce((o,a)=>{o[a.name]=a.value; return o;}, {}),
                        });
                    }
                }
                return matches;
            }
        """)
        with open(os.path.join(OUT_DIR, 'sandgold_matches.json'), 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'砂金 matches: {len(result)} elements')
        for m in result[:5]:
            print('  -', m['tag'], 'cls=', m['className'][:80] if isinstance(m['className'], str) else m['className'], 'attrs=', list(m['attrs'].keys())[:6])

        # 4. Capture overall vote-related structure: how many candidate-like cards exist
        survey = await page.evaluate("""
            () => {
                // Look for elements that probably represent a candidate.
                // Heuristics: contain a name + something that looks like a vote control.
                const candidateRoots = [];
                // Vue components often use kebab-case class names; gather any
                // class that occurs >50 times (heavy repetition = list items)
                const classCount = {};
                document.querySelectorAll('*').forEach(el => {
                    if (typeof el.className === 'string') {
                        el.className.trim().split(/\\s+/).forEach(c => {
                            if (c) classCount[c] = (classCount[c] || 0) + 1;
                        });
                    }
                });
                const repeated = Object.entries(classCount)
                    .filter(([_, n]) => n >= 30 && n <= 200)
                    .sort((a,b) => b[1]-a[1])
                    .slice(0, 30);

                // Look for any modal/dialog templates in raw HTML
                const dlgKeys = ['确定投给TA吗', '确认投票', '我再想想', '人气王', '投票'];
                const dlgHits = {};
                dlgKeys.forEach(k => {
                    const found = Array.from(document.querySelectorAll('*'))
                        .filter(e => (e.innerText||'').includes(k) && e.children.length < 5);
                    dlgHits[k] = found.slice(0, 3).map(el => ({
                        tag: el.tagName.toLowerCase(),
                        cls: el.className,
                        attrs: Array.from(el.attributes || []).reduce((o,a)=>{o[a.name]=a.value; return o;}, {}),
                        outer: el.outerHTML.slice(0, 300),
                    }));
                });

                return {
                    repeatedClasses: repeated,
                    dialogHits: dlgHits,
                    totalElements: document.querySelectorAll('*').length,
                    bodyText: document.body.innerText.slice(0, 1500),
                };
            }
        """)
        with open(os.path.join(OUT_DIR, 'survey.json'), 'w', encoding='utf-8') as f:
            json.dump(survey, f, ensure_ascii=False, indent=2)
        print(f'survey: {survey["totalElements"]} elements, '
              f'{len(survey["repeatedClasses"])} repeated classes')
        print('top repeated classes:')
        for cls, cnt in survey['repeatedClasses'][:15]:
            print(f'  {cnt:4d}  .{cls}')

        # 5. dump network calls
        with open(os.path.join(OUT_DIR, 'api_calls.json'), 'w', encoding='utf-8') as f:
            json.dump(api_calls, f, ensure_ascii=False, indent=2)
        print(f'api-relevant requests captured: {len(api_calls)}')

        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
