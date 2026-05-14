"""用一个全新代理 IP 实际调一次 /Active2551/Vote，看服务器返回什么。

目标：搞清楚 "IP 已经使用" 这句话是从哪里来的。
- 神龙代理 API？(不太可能，已验证返回是正常 ip:port 列表)
- starrailawards 服务器对该 IP 的投票限制？
- 还是别的地方？
"""
import os, sys, re, time
import httpx
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(
    os.path.normpath(os.path.join(HERE, "..", "reconstructed", "config.yaml")),
    "r", encoding="utf-8")) or {}

# 1) 拉一个新代理
print("[1] 拉代理 ...")
r = httpx.get(CFG["proxy_api_url"], timeout=10.0)
ip_ports = [ln.strip() for ln in r.text.strip().splitlines()
            if re.match(r"^[\w\.\-]+:\d+$", ln.strip())]
print(f"    拿到 {len(ip_ports)} 个，用第 1 个: {ip_ports[0]}")
proxy_url = f"http://{ip_ports[0]}"

# 2) 先访问主页拿 session cookie（模拟正常浏览器流程）
print(f"\n[2] 通过代理访问主页，拿 cookies ...")
with httpx.Client(proxy=proxy_url, timeout=20.0, verify=False,
                  follow_redirects=True) as client:
    r = client.get("https://www.starrailawards.com/Vote2026/index.html",
                   headers={
                       "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                                     "Chrome/148.0.0.0 Safari/537.36",
                   })
    print(f"    GET / → HTTP {r.status_code}, body bytes={len(r.content)}")
    print(f"    Set-Cookie / 已有 Cookie: {dict(client.cookies)}")

    # 3) 投一票
    print(f"\n[3] 发起 /Active2551/Vote (vid=51 砂金) ...")
    r2 = client.post(
        "https://www.starrailawards.com/Active2551/Vote",
        headers={
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "x-requested-with": "XMLHttpRequest",
            "referer": "https://www.starrailawards.com/Vote2026/index.html",
            "origin": "https://www.starrailawards.com",
        },
        content="gp=&vid=51",
    )
    print(f"    HTTP {r2.status_code}")
    print(f"    Response Headers: {dict(r2.headers)}")
    print(f"    Response Body (前 500 字符):")
    print("    " + repr(r2.text[:500]))
    print()
    if "已" in r2.text or "投" in r2.text or "ip" in r2.text.lower() or "limit" in r2.text.lower():
        print(f"    >>> 命中关键词：服务器明确告诉你这个 IP 被限制了 / 已投过 <<<")
