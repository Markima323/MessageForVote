"""List the candidates currently shown on the vote page.

Use this anytime the site advances to a new round (e.g. /Vote2026 →
/Vote2026Final or similar): it re-probes the live DOM and writes the
authoritative (name, dom_index) mapping to candidates.json next to
this script. The GUI reads the same file on startup so it can display
the latest mapping as a hint.

Usage:
    python list_candidates.py
    python list_candidates.py --url https://www.starrailawards.com/Vote2026/index.html
    python list_candidates.py --json   (emit machine-readable JSON only)
"""
import argparse
import asyncio
import json
import os
import sys
import time

from playwright.async_api import async_playwright


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_URL = "https://www.starrailawards.com/Vote2026/index.html"
OUT_PATH = os.path.join(HERE, "candidates.json")


async def probe(url: str) -> dict:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0 Safari/537.36",
                viewport={"width": 1280, "height": 1600},
                locale="zh-CN",
            )
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            await asyncio.sleep(2.5)  # let Vue settle

            entries = await page.evaluate("""
                () => {
                    const cards = Array.from(document.querySelectorAll('.character-card'));
                    return cards.map((c, i) => {
                        const img = c.querySelector('img');
                        const name = (c.querySelector('.character-name')?.innerText || '').trim();
                        const key = img ? img.src.split('/').pop().split('.')[0] : null;
                        return { index: i, name, image_key: key };
                    });
                }
            """)
            return {
                "url": url,
                "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "count": len(entries),
                "entries": entries,
            }
        finally:
            await browser.close()


def print_table(snapshot: dict):
    print(f"URL          : {snapshot['url']}")
    print(f"Captured at  : {snapshot['captured_at']}")
    print(f"Total cards  : {snapshot['count']}")
    print()
    print(f"{'idx':>3s}  {'name':<14s}  image-key")
    print("-" * 50)
    for e in snapshot["entries"]:
        print(f"{e['index']:>3d}  {e['name']:<14s}  {e['image_key'] or '-'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON only (no human table)")
    args = ap.parse_args()

    snapshot = asyncio.run(probe(args.url))
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    if args.json:
        json.dump(snapshot, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_table(snapshot)
        print(f"\nSaved to: {OUT_PATH}")


if __name__ == "__main__":
    main()
