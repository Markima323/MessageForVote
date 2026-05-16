"""探测后置 API 接口的可达性（不解 captcha，只看路由 + 错误码）。

每个接口在零凭证状态下应该返回 errCode=-3 "请先完成验证码" 这类
JSON 错误。如果返回 404、500，或被改成完全不同的 errMsg，
说明接口路径或鉴权机制变了。
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
print(f"[*] 代理池 {len(ip_ports)} 个，逐个试直到能打开页面\n")

JS_TEMPLATE = r"""async ([url, body]) => {
    const r = await fetch(url, {
        headers: {
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "x-requested-with": "XMLHttpRequest",
        },
        body: body,
        method: "POST",
        credentials: "include",
    });
    let text = "";
    try { text = await r.text(); } catch (e) {}
    return { status: r.status, body: text.slice(0, 240) };
}"""

CASES = [
    ("评分 /Top",            "https://www.starrailawards.com/Active2551/Top",            "tp=2&st=206&score=10&msg="),
    ("截图 /SaveTierList",   "https://www.starrailawards.com/Active2551/SaveTierList",   "snapshot=%7B%22S%22%3A%5B%5D%7D"),
    ("点赞 /Zan",            "https://www.starrailawards.com/Active2551/Zan",            "id=1"),
    ("PK 数据 /GetPkData",   "https://www.starrailawards.com/Active2551/GetPkData",      "tp=2"),
    ("PK 投币 /Pk2",         "https://www.starrailawards.com/Active2551/Pk2",            "vid=51"),
    ("PK 刷新 /RefreshPk",   "https://www.starrailawards.com/Active2551/RefreshPk",      "tp=2"),
]


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = None
            for ipp in ip_ports[:8]:
                proxy_url = f"http://{ipp}"
                print(f"  尝试代理 {proxy_url} ...", end=" ", flush=True)
                ctx = await browser.new_context(proxy={"server": proxy_url})
                try:
                    page = await ctx.new_page()
                    await page.goto(
                        "https://www.starrailawards.com/Vote2026/index.html",
                        wait_until="domcontentloaded", timeout=20_000)
                    print("✓")
                    break
                except Exception as e:
                    print(f"✗ {type(e).__name__}")
                    try:
                        await ctx.close()
                    except Exception:
                        pass
                    page = None
            if page is None:
                print("\n[ERROR] 所有代理都打不开页面")
                return
            await page.wait_for_timeout(2500)
            print()

            for label, url, body in CASES:
                try:
                    res = await page.evaluate(JS_TEMPLATE, [url, body])
                    status = res.get("status")
                    body_text = res.get("body") or ""
                    verdict = "?"
                    if status == 200 and '"errCode"' in body_text:
                        verdict = "✓ 接口存活（返回 JSON 错误码）"
                    elif status == 200:
                        verdict = "✓ HTTP 200，但返回不是 JSON"
                    elif status == 404:
                        verdict = "✗ 404 接口不存在 / 已重命名"
                    elif status >= 500:
                        verdict = f"✗ {status} 服务端异常"
                    print(f"  [{status:>3}] {label:<22} {verdict}")
                    print(f"        body={body_text[:160]!r}")
                except Exception as e:
                    print(f"  [ERR] {label:<22} {type(e).__name__}: {str(e)[:120]}")
        finally:
            await browser.close()


asyncio.run(main())
