"""三组对照实验，搞清服务器按什么维度识别 '同一网络'。

实验 A: 全新代理 IP + 全新 cookie  → 基准：第一票服务器返回什么
实验 B: 同 IP + 全新 cookie         → 服务器看 IP？
实验 C: 不同 IP + 同 cookie         → 服务器看 cookie？

每次投票后打印服务器返回的 errCode + msg，最关心是否出现
'当前网络已有玩家参与' 或类似话术。
"""
import asyncio, re, os, json
import httpx, yaml
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(
    os.path.normpath(os.path.join(HERE, "..", "reconstructed", "config.yaml")),
    "r", encoding="utf-8")) or {}


def fetch_proxies():
    r = httpx.get(CFG["proxy_api_url"], timeout=10.0)
    return [ln.strip() for ln in r.text.strip().splitlines()
            if re.match(r"^[\w\.\-]+:\d+$", ln.strip())]


VOTE_JS = """async (vid) => {
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
    return { status: r.status, body: await r.text() };
}"""

EXIT_IP_JS = """async () => {
    try {
        const r = await fetch("https://api.ipify.org");
        return await r.text();
    } catch (e) { return "?"; }
}"""


async def open_page(browser, proxy_url, preset_cookie=None,
                    preset_storage=None):
    ctx = await browser.new_context(proxy={"server": proxy_url})
    if preset_cookie:
        await ctx.add_cookies([preset_cookie])
    page = await ctx.new_page()
    if preset_storage:
        await page.goto("https://www.starrailawards.com/Vote2026/index.html",
                        wait_until="domcontentloaded", timeout=60_000)
        await page.evaluate(
            "(s) => Object.entries(s).forEach(([k,v]) => localStorage.setItem(k,v))",
            preset_storage)
    await page.goto("https://www.starrailawards.com/Vote2026/index.html",
                    wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(2000)  # 让 JS 设 cookie
    return ctx, page


async def snapshot(ctx, page):
    cookies = await ctx.cookies()
    uuid_c = next((c for c in cookies
                   if c["name"] == "Battle2vPsvote2026_uuid"), None)
    storage = await page.evaluate("""() => {
        const o = {};
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            o[k] = localStorage.getItem(k);
        }
        return o;
    }""")
    exit_ip = await page.evaluate(EXIT_IP_JS)
    return uuid_c, storage, exit_ip


async def main():
    proxies = fetch_proxies()
    p_a = f"http://{proxies[0]}"
    p_b = f"http://{proxies[5]}"
    print(f"[*] 代理 A: {p_a}")
    print(f"[*] 代理 B: {p_b}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            # --- 实验 A: 新 IP + 新 cookie ---
            print("=== 实验 A: 代理 A + 全新 cookie ===")
            ctx_a, page_a = await open_page(browser, p_a)
            uuid_a, ls_a, exit_a = await snapshot(ctx_a, page_a)
            print(f"   出口 IP: {exit_a}")
            print(f"   uuid cookie: {uuid_a['value'] if uuid_a else 'NONE'}")
            res_a = await page_a.evaluate(VOTE_JS, 51)
            print(f"   投票返回: status={res_a['status']}, body={res_a['body']!r}")
            await ctx_a.close()

            # --- 实验 B: 同 IP A + 全新 cookie（独立 context，cookie 不共享）
            print("\n=== 实验 B: 代理 A + 全新 cookie（新 context）===")
            ctx_b, page_b = await open_page(browser, p_a)
            uuid_b, ls_b, exit_b = await snapshot(ctx_b, page_b)
            print(f"   出口 IP: {exit_b} (跟 A 相同？{exit_b == exit_a})")
            print(f"   uuid cookie: {uuid_b['value'] if uuid_b else 'NONE'}")
            res_b = await page_b.evaluate(VOTE_JS, 51)
            print(f"   投票返回: status={res_b['status']}, body={res_b['body']!r}")
            await ctx_b.close()

            # --- 实验 C: 代理 B + 复用 A 的 cookie + storage
            print("\n=== 实验 C: 代理 B + 复用 A 的 cookie+storage ===")
            cookie_for_c = None
            if uuid_a:
                cookie_for_c = {
                    "name": uuid_a["name"],
                    "value": uuid_a["value"],
                    "domain": uuid_a["domain"],
                    "path": uuid_a["path"],
                }
            ctx_c, page_c = await open_page(
                browser, p_b,
                preset_cookie=cookie_for_c,
                preset_storage=ls_a,
            )
            uuid_c, ls_c, exit_c = await snapshot(ctx_c, page_c)
            print(f"   出口 IP: {exit_c} (跟 A {'相同' if exit_c == exit_a else '不同'})")
            print(f"   uuid cookie: {uuid_c['value'] if uuid_c else 'NONE'} "
                  f"(跟 A {'相同' if uuid_c and uuid_a and uuid_c['value']==uuid_a['value'] else '不同'})")
            res_c = await page_c.evaluate(VOTE_JS, 51)
            print(f"   投票返回: status={res_c['status']}, body={res_c['body']!r}")
            await ctx_c.close()

            print("\n=== 分析 ===")
            print("如果 A 已成功投票，B 失败 → 服务器认 IP（同 IP 不让再投）")
            print("如果 A 已成功投票，C 失败 → 服务器认 cookie（同 cookie 不让再投）")
            print("如果 A 就直接被拒'已参与'  → 这个 IP 段早被人用过/被拉黑")
        finally:
            await browser.close()


asyncio.run(main())
