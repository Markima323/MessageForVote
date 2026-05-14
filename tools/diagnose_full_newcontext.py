"""完全照搬主脚本 _new_context 的配置（stealth + route + init_script），
看投票返回有没有变化。

主脚本和我之前的诊断差异：
  + Stealth().apply_stealth_async(ctx)
  + ctx.add_init_script(captcha CSS fix)
  + ctx.route("**/*", abort font/media + appoint.icu image)
"""
import asyncio, os, re
import httpx, yaml
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(
    os.path.normpath(os.path.join(HERE, "..", "reconstructed", "config.yaml")),
    "r", encoding="utf-8")) or {}

r = httpx.get(CFG["proxy_api_url"], timeout=10.0)
ip_ports = [ln.strip() for ln in r.text.strip().splitlines()
            if re.match(r"^[\w\.\-]+:\d+$", ln.strip())]
proxy_url = f"http://{ip_ports[0]}"
print(f"[*] 代理: {proxy_url}\n")


CAPTCHA_INIT = """
(() => {
    window.__captchaOffsetX = 0;
    window.__captchaOffsetY = 0;
    function fixAll() { /* noop in this test */ }
    window.__captchaFixAll = fixAll;
})();
"""


async def main():
    async with async_playwright() as pw:
        launch_kwargs = dict(
            headless=False,
            executable_path=CFG.get("browser_path") or None,
        )
        if not launch_kwargs["executable_path"]:
            launch_kwargs.pop("executable_path")
        browser = await pw.chromium.launch(**launch_kwargs)

        try:
            print("=== 配置 1: 完全照搬主脚本 _new_context ===")
            ctx = await browser.new_context(proxy={"server": proxy_url})
            await Stealth().apply_stealth_async(ctx)
            await ctx.add_init_script(CAPTCHA_INIT)

            async def _route(route):
                req = route.request
                rt = req.resource_type
                url = req.url
                if rt in ("font", "media"):
                    await route.abort()
                    return
                if rt == "image" and "static.appoint.icu" in url:
                    await route.abort()
                    return
                await route.continue_()
            await ctx.route("**/*", _route)

            page = await ctx.new_page()

            # 监听 network，看页面发了哪些请求 + 响应
            seen = []
            page.on("response", lambda r: seen.append(
                (r.status, r.url, r.request.method)))

            await page.goto("https://www.starrailawards.com/Vote2026/index.html",
                            wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3000)

            cookies = await ctx.cookies()
            uuid_c = next((c for c in cookies if c["name"].startswith("Battle")), None)
            print(f"   uuid cookie: {uuid_c['value'] if uuid_c else 'NONE'}")

            ls = await page.evaluate("""() => {
                const o = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    o[k] = localStorage.getItem(k);
                }
                return o;
            }""")
            print(f"   localStorage: {ls}")

            exit_ip = await page.evaluate("""async () => {
                try { return (await (await fetch("https://api.ipify.org")).text()).trim(); }
                catch (e) { return "?"; }
            }""")
            print(f"   出口 IP: {exit_ip}")

            res = await page.evaluate("""async (vid) => {
                const r = await fetch("https://www.starrailawards.com/Active2551/Vote", {
                    headers: {
                        "accept": "*/*",
                        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "x-requested-with": "XMLHttpRequest",
                    },
                    body: "gp=&vid=" + vid,
                    method: "POST",
                    credentials: "include",
                });
                return await r.text();
            }""", 51)
            print(f"   投票返回: {res!r}")

            print(f"\n   页面发起的请求总计: {len(seen)} 条")
            # 看 starrailawards 域名下的所有请求
            print("   starrailawards.com 域下的请求：")
            for status, url, method in seen:
                if "starrailawards" in url:
                    short = url.replace("https://www.starrailawards.com", "")[:80]
                    print(f"     [{status}] {method} {short}")

            await ctx.close()
        finally:
            await browser.close()


asyncio.run(main())
