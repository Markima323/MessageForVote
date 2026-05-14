"""验证：访问主页 + Region + pv 后，能否拿到 session cookie 让投票通过。"""
import os, re
import httpx, yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(
    os.path.normpath(os.path.join(HERE, "..", "reconstructed", "config.yaml")),
    "r", encoding="utf-8")) or {}

r = httpx.get(CFG["proxy_api_url"], timeout=10.0)
ip_ports = [ln.strip() for ln in r.text.strip().splitlines()
            if re.match(r"^[\w\.\-]+:\d+$", ln.strip())]
proxy_url = f"http://{ip_ports[0]}"
print(f"[*] 用代理: {proxy_url}\n")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

with httpx.Client(proxy=proxy_url, timeout=20.0, verify=False,
                  follow_redirects=True,
                  headers={"user-agent": UA}) as c:
    # 1) 主页
    r1 = c.get("https://www.starrailawards.com/Vote2026/index.html")
    print(f"[1] GET / → {r1.status_code}, cookies: {dict(c.cookies)}")

    # 2) Region + pv (脚本里 async 加载，跟主页同 origin)
    for path in ["/Active2551/Region", "/Active2551/pv"]:
        r = c.get(f"https://www.starrailawards.com{path}",
                  headers={"referer": "https://www.starrailawards.com/Vote2026/index.html"})
        print(f"[2] GET {path} → {r.status_code}, body={r.text[:120]!r}, cookies: {dict(c.cookies)}")

    # 3) 投票
    r3 = c.post(
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
    print(f"\n[3] POST /Active2551/Vote → {r3.status_code}")
    print(f"    Body: {r3.text!r}")
