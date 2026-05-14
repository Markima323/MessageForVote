"""调一次 /Top 评分接口，看返回里有没有 id 字段供 /Zan 用。"""
import asyncio, os, re, json
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


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(proxy={"server": proxy_url})
            page = await ctx.new_page()
            await page.goto("https://www.starrailawards.com/Vote2026/index.html",
                            wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(2500)

            # 评分
            top = await page.evaluate("""async () => {
                const r = await fetch("https://www.starrailawards.com/Active2551/Top", {
                    headers: {
                        "accept": "*/*",
                        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "x-requested-with": "XMLHttpRequest",
                    },
                    body: "tp=2&st=206&score=10&msg=",
                    method: "POST",
                    credentials: "include",
                });
                return { status: r.status, body: await r.text() };
            }""")
            print(f"[Top] status={top['status']}")
            print(f"[Top] body={top['body']}")
            try:
                obj = json.loads(top["body"])
                print(f"[Top] 解析后: {json.dumps(obj, ensure_ascii=False, indent=2)}")
                if isinstance(obj, dict):
                    for k in ("id", "Id", "ID"):
                        if k in obj:
                            print(f"\n>>> 找到顶层 {k} = {obj[k]} <<<")
                    if "data" in obj and isinstance(obj["data"], dict):
                        for k in ("id", "Id", "ID"):
                            if k in obj["data"]:
                                print(f"\n>>> 找到 data.{k} = {obj['data'][k]} <<<")
            except Exception as e:
                print(f"[Top] 不是 JSON: {e}")
        finally:
            await browser.close()


asyncio.run(main())
