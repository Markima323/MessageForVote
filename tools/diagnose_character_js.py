"""验证 character.js 能否通过代理拉到、能否解析出 {name: vid}。

这是 _read_character_data 修复后的预演——通过 httpx + 神龙代理，
直接拉 character.js，照搬新代码的解析逻辑。
"""
import os, sys, json, re
import httpx
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(
    os.path.normpath(os.path.join(HERE, "..", "reconstructed", "config.yaml")),
    "r", encoding="utf-8")) or {}

# 拉一批代理
print("[1] 拉代理 ...")
ip_ports = []
try:
    r = httpx.get(CFG["proxy_api_url"], timeout=10.0)
    for ln in r.text.strip().splitlines():
        ln = ln.strip()
        if re.match(r"^[\w\.\-]+:\d+$", ln):
            ip_ports.append(ln)
    print(f"    拿到 {len(ip_ports)} 个")
except Exception as e:
    print(f"    FAILED: {e}")
    sys.exit(1)
if not ip_ports:
    sys.exit(1)

# 用代理拉 character.js
URLS = [
    "https://static.appoint.icu/Railvote/character.js?v=3",
    "https://static.appoint.icu/Railvote/character.js",
]
proxy_url = f"http://{ip_ports[0]}"
print(f"\n[2] 通过 {proxy_url} 拉 character.js ...")
text = None
for u in URLS:
    try:
        r = httpx.get(u, proxy=proxy_url, timeout=20.0, verify=False)
        print(f"    GET {u} → HTTP {r.status_code}, bytes={len(r.content)}")
        if r.status_code == 200 and r.content:
            text = r.text
            break
    except Exception as e:
        print(f"    {u} → {type(e).__name__}: {e}")
if not text:
    print("[2] 所有 URL 都拿不到，下一步靠 Playwright 的 page.request 是否能成功，纯 HTTP 这条路堵住")
    sys.exit(1)

# 切出 JSON 数组
i, j = text.find("["), text.rfind("]")
print(f"\n[3] 文本长度={len(text)}, [={i}, ]={j}")
if i < 0 or j <= i:
    print("    没找到 [ ... ]")
    sys.exit(1)
try:
    data = json.loads(text[i:j + 1])
    print(f"    JSON parse OK，{len(data)} 条记录")
except Exception as e:
    print(f"    JSON parse FAILED: {e}")
    print("    前 300 字符:", text[i:i+300])
    sys.exit(1)

# 构造 name → vid 映射并查一下你的目标
out = {item["name"]: item for item in data
       if isinstance(item, dict) and item.get("name")}
target = CFG.get("target_character_name") or "砂金"
targets_multi = CFG.get("target_character_names") or [target]
print(f"\n[4] 验证目标角色:")
for n in targets_multi:
    info = out.get(n)
    if info:
        print(f"    ✓ {n} → vid={info.get('id')}, image={info.get('image')}, gender={info.get('gender','?')}")
    else:
        print(f"    ✗ {n} → 没找到")
print(f"\n[OK] 改完后的脚本应该能正常拿到 vid。")
