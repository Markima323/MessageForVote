"""用 Playwright 通过代理打开主页，dump 所有 cookie + 关键 storage，
找出浏览器和 httpx 在 session 上的差别。"""
import asyncio, os, re
import httpx, yaml
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(
    os.path.normpath(os.path.join(HERE, "..", "reconstructed", "config.yaml")),
    "r", encoding="utf-8")) or {}

r = httpx.get(CFG["proxy_api_url"], timeout=10.0)
ip_ports = [ln.strip() for ln in r.text.strip().splitlines()
            if re.match(r"^[\w\.\-]+:\d+$", ln.strip())]
proxy_url = f"http://{ip_ports[0]}"
print(f"[*] 用代理: {proxy_url}\n")

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(proxy={"server": proxy_url})
            page = await ctx.new_page()
            await page.goto("https://www.starrailawards.com/Vote2026/index.html",
                            wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3000)  # 等 JS 完全跑完

            print("[1] page.context.cookies():")
            for c in await ctx.cookies():
                print(f"    {c}")

            print("\n[2] document.cookie:")
            doc = await page.evaluate("document.cookie")
            print(f"    {doc!r}")

            print("\n[3] localStorage:")
            ls = await page.evaluate("""() => {
                const out = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    out[k] = localStorage.getItem(k);
                }
                return out;
            }""")
            print(f"    {ls}")

            print("\n[4] sessionStorage:")
            ss = await page.evaluate("""() => {
                const out = {};
                for (let i = 0; i < sessionStorage.length; i++) {
                    const k = sessionStorage.key(i);
                    out[k] = sessionStorage.getItem(k);
                }
                return out;
            }""")
            print(f"    {ss}")

            # 浏览器内直接 fetch 一次投票，看 errCode 是多少
            print("\n[5] in-browser fetch /Active2551/Vote (vid=51):")
            res = await page.evaluate("""async () => {
                const r = await fetch("https://www.starrailawards.com/Active2551/Vote", {
                    headers: {
                        "accept": "*/*",
                        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "x-requested-with": "XMLHttpRequest",
                    },
                    body: "gp=&vid=51",
                    method: "POST",
                    credentials: "include",
                });
                return { status: r.status, body: await r.text() };
            }""")
            print(f"    status={res['status']}")
            print(f"    body={res['body']!r}")
        finally:
            await browser.close()

asyncio.run(main())
