"""验证 Playwright 浏览器真实出口 IP 是不是代理 IP。

Playwright 在 Chromium 上的一个已知坑：
    per-context proxy 要求 browser launch 时也带 proxy 参数（哪怕是占位的
    'per-context'），否则 Chromium 直接走系统直连，context 的 proxy 设置
    被静默忽略。如果脚本没注意这点，浏览器就用本机 IP。
"""
import asyncio, os, re
import httpx, yaml
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(
    os.path.normpath(os.path.join(HERE, "..", "reconstructed", "config.yaml")),
    "r", encoding="utf-8")) or {}

# 拿一个代理 + 本机 IP
r = httpx.get(CFG["proxy_api_url"], timeout=10.0)
ip_ports = [ln.strip() for ln in r.text.strip().splitlines()
            if re.match(r"^[\w\.\-]+:\d+$", ln.strip())]
proxy_ip = ip_ports[0].split(":")[0]
proxy_url = f"http://{ip_ports[0]}"
print(f"[*] 代理: {proxy_url}  (proxy host IP = {proxy_ip})")

# 本机出口 IP，作为对照
try:
    local_ip = httpx.get("https://api.ipify.org", timeout=10).text.strip()
    print(f"[*] 本机出口 IP: {local_ip}")
except Exception as e:
    print(f"[*] 本机 IP 拿不到: {e}")
    local_ip = "?"
print()


async def check(launch_kwargs_extra: dict, label: str):
    """对比：browser launch 不带 proxy vs 带 'per-context' 占位 proxy"""
    print(f"=== {label} ===")
    async with async_playwright() as pw:
        kwargs = dict(headless=True, **launch_kwargs_extra)
        browser = await pw.chromium.launch(**kwargs)
        try:
            ctx = await browser.new_context(proxy={"server": proxy_url})
            page = await ctx.new_page()
            try:
                await page.goto("https://api.ipify.org", timeout=30_000)
                exit_ip = (await page.content()).strip()
                # ipify wraps in <html><body>; pull just the IP
                m = re.search(r"\d+\.\d+\.\d+\.\d+", exit_ip)
                exit_ip = m.group(0) if m else exit_ip[:80]
                verdict = "✓ 通过代理" if exit_ip == proxy_ip else \
                          ("✗ 用了本机 IP" if exit_ip == local_ip else
                           "? 出口是别的 IP")
                print(f"   浏览器出口 IP = {exit_ip}  → {verdict}")
            except Exception as e:
                print(f"   page.goto 失败: {type(e).__name__}: {str(e)[:160]}")
        finally:
            await browser.close()
    print()


async def main():
    # 当前主脚本的写法：launch 不传 proxy
    await check({}, "A. launch 不传 proxy（当前主脚本做法）")
    # 推荐写法：launch 带 'per-context' 占位
    await check({"proxy": {"server": "per-context"}},
                "B. launch 带 proxy='per-context' 占位")

asyncio.run(main())
