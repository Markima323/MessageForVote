"""验证：全新 context（零 cookie + 神龙代理 IP），第一票服务器到底返回什么？

预期对比：
- 如果 errCode=-3 "请先完成验证码" → 这个 IP 是干净的，服务器允许继续走验证码流程
- 如果错误信息含 "已参与" / "已投" / "网络" → IP 被拉黑，跟 cookie 干不干净无关
"""
import asyncio, re, os
import httpx, yaml
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(
    os.path.normpath(os.path.join(HERE, "..", "reconstructed", "config.yaml")),
    "r", encoding="utf-8")) or {}

r = httpx.get(CFG["proxy_api_url"], timeout=10.0)
ip_ports = [ln.strip() for ln in r.text.strip().splitlines()
            if re.match(r"^[\w\.\-]+:\d+$", ln.strip())]
print(f"[*] 拿到 {len(ip_ports)} 个代理，挨个测试，看有多少个能跑通到 errCode=-3\n")


async def try_one(proxy_ipp: str):
    proxy_url = f"http://{proxy_ipp}"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(proxy={"server": proxy_url})
            page = await ctx.new_page()
            try:
                await page.goto(
                    "https://www.starrailawards.com/Vote2026/index.html",
                    wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2000)
                exit_ip = await page.evaluate("""async () => {
                    try { return (await (await fetch("https://api.ipify.org")).text()).trim(); }
                    catch (e) { return "?"; }
                }""")
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
                return exit_ip, res
            except Exception as e:
                return None, f"PAGE_ERR: {type(e).__name__}: {str(e)[:80]}"
        finally:
            await browser.close()


async def main():
    stats = {"-3": 0, "blocked": 0, "other": 0, "err": 0}
    for i, ipp in enumerate(ip_ports[:8]):  # 测前 8 个
        exit_ip, body = await try_one(ipp)
        if exit_ip is None:
            verdict = "✗ 浏览器/代理失败"
            stats["err"] += 1
        elif '"errCode":-3' in body:
            verdict = "✓ 允许投票（请先完成验证码）"
            stats["-3"] += 1
        elif any(k in body for k in ["已参与", "已投", "网络", "本次活动", "已经", "limit", "block"]):
            verdict = "✗ IP 被服务器视作'已用'"
            stats["blocked"] += 1
        else:
            verdict = "? 其它"
            stats["other"] += 1
        print(f"  [{i+1}] 入口 {ipp:<26}  出口 {exit_ip or '-':<16}  →  {verdict}")
        print(f"      body={body[:160]!r}")
    print()
    print(f"[统计] 允许投票: {stats['-3']}  /  被认为已使用: {stats['blocked']}  /  其它: {stats['other']}  /  失败: {stats['err']}")
    if stats["blocked"] >= stats["-3"]:
        print("\n>>> 多数代理 IP 在零 cookie 状态下已经被服务器拒，说明 IP 段被打标")
        print(">>> 跟 cookie 清不清无关。问题在代理质量。")
    else:
        print("\n>>> 多数代理 IP 是干净的，理论上脚本应该能正常工作")


asyncio.run(main())
