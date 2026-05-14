"""模拟主脚本的 launch 配置（用本机 Chrome），dump 出新 context 的 cookie 起始状态。

如果本机 Chrome 当前正在运行，且 Playwright 没用独立 user-data-dir，
新 context 可能会继承本机 Chrome 已有的 starrailawards cookie。
"""
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
browser_path = CFG.get("browser_path", "")
print(f"[*] 代理: {proxy_url}")
print(f"[*] 浏览器: {browser_path}\n")


async def main():
    async with async_playwright() as pw:
        # 跟主脚本一模一样的 launch 方式
        launch_kwargs = dict(headless=False)
        if browser_path and os.path.isfile(browser_path):
            launch_kwargs["executable_path"] = browser_path
        browser = await pw.chromium.launch(**launch_kwargs)
        try:
            ctx = await browser.new_context(proxy={"server": proxy_url})
            page = await ctx.new_page()

            # 在 navigate 之前，先看 context 初始 cookie 是不是空
            init_cookies = await ctx.cookies()
            print(f"[1] navigate 前 ctx.cookies() = {init_cookies}")

            await page.goto("https://www.starrailawards.com/Vote2026/index.html",
                            wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3000)

            after_cookies = await ctx.cookies()
            print(f"\n[2] navigate 后 ctx.cookies():")
            for c in after_cookies:
                print(f"    {c['name']} = {c['value'][:60]}  (domain={c['domain']})")

            ls = await page.evaluate("""() => {
                const o = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    o[k] = localStorage.getItem(k);
                }
                return o;
            }""")
            print(f"\n[3] localStorage = {ls}")

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
            print(f"\n[4] 投票返回: {res!r}")

            if "已参与" in res or "网络" in res or "已经" in res:
                print("\n>>> 服务器报'已参与'，配合上面 cookie/localStorage 的内容看哪些是脏的")
            elif '"errCode":-3' in res:
                print("\n>>> 服务器允许投票，cookie 状态干净，问题不在 cookie")
        finally:
            await browser.close()


asyncio.run(main())
