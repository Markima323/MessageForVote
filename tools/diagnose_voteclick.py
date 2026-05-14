"""模拟主脚本：用代理打开页面，真的去点 .vote-btn 按钮，
监听点击瞬间发出的所有请求 + 页面上弹出的 alert/modal 文字。

如果"已参与"是从这里冒出来的，能直接定位到根因。
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
print(f"[*] 代理: {proxy_url}")

# 用本轮存在的角色（候选名单第 1 个）
import json
cands = json.load(open(os.path.normpath(os.path.join(
    HERE, "..", "reconstructed", "candidates.json")), "r", encoding="utf-8"))
target_name = cands["entries"][0]["name"]
print(f"[*] 目标角色: {target_name}\n")


async def main():
    async with async_playwright() as pw:
        kwargs = dict(headless=False)
        if CFG.get("browser_path") and os.path.isfile(CFG["browser_path"]):
            kwargs["executable_path"] = CFG["browser_path"]
        browser = await pw.chromium.launch(**kwargs)
        try:
            ctx = await browser.new_context(proxy={"server": proxy_url})
            await Stealth().apply_stealth_async(ctx)
            page = await ctx.new_page()

            # 全程记录请求 + 响应
            timeline = []
            async def on_resp(r):
                if "starrailawards" not in r.url:
                    return
                try:
                    body = await r.text()
                except Exception:
                    body = "<binary>"
                timeline.append((r.request.method, r.url, r.status, body[:200]))
            page.on("response", lambda r: asyncio.create_task(on_resp(r)))

            await page.goto("https://www.starrailawards.com/Vote2026/index.html",
                            wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3000)

            print("==== 加载页面后的请求 ====")
            for m, u, s, b in timeline:
                short = u.replace("https://www.starrailawards.com", "")[:60]
                print(f"  [{s}] {m} {short}")
                if b and not b.startswith("<"):
                    print(f"        body: {b[:160]!r}")
            timeline.clear()

            # 找卡片并点 vote-btn
            print(f"\n==== 现在点 '{target_name}' 的 .vote-btn 按钮 ====")
            card = page.locator(".character-card", has_text=target_name).first
            await card.scroll_into_view_if_needed()
            await card.locator(".vote-btn").click()

            # 等 5 秒收集响应
            await page.wait_for_timeout(5000)

            print("\n==== 点击后 5 秒内的 starrailawards 请求 ====")
            for m, u, s, b in timeline:
                short = u.replace("https://www.starrailawards.com", "")[:60]
                print(f"  [{s}] {m} {short}")
                if b and not b.startswith("<"):
                    print(f"        body: {b[:200]!r}")

            # 看页面上的弹窗/提示文字
            print("\n==== 页面上的弹窗/提示文字 ====")
            popup_texts = await page.evaluate("""() => {
                const out = [];
                document.querySelectorAll('.custom-alert-overlay2, .custom-alert-box, '
                    + '.window-show, .mask-show, [class*="alert"], [class*="modal"], '
                    + '[class*="popup"], [class*="toast"], [class*="tip"]')
                    .forEach(el => {
                        const visible = el.offsetParent !== null;
                        const txt = (el.innerText || '').trim();
                        if (txt) out.push({
                            visible,
                            cls: el.className,
                            text: txt.slice(0, 200)
                        });
                    });
                return out;
            }""")
            for p in popup_texts:
                print(f"  visible={p['visible']}  class={p['cls'][:50]!r}")
                print(f"    text: {p['text']!r}")

            print("\n[按 Ctrl+C 关闭，或等 10 秒自动关] ")
            await page.wait_for_timeout(10000)
        finally:
            await browser.close()


asyncio.run(main())
