"""多测几个不同入口的代理，看实际出口 IP 池有多大。

如果 30 个入口背后只有 1-2 个出口，说明这家代理是隧道共享型——
对 starrailawards 这种按 IP 限投票的网站完全不适用。
"""
import re
import httpx
import yaml
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(
    os.path.normpath(os.path.join(HERE, "..", "reconstructed", "config.yaml")),
    "r", encoding="utf-8")) or {}

r = httpx.get(CFG["proxy_api_url"], timeout=10.0)
ip_ports = [ln.strip() for ln in r.text.strip().splitlines()
            if re.match(r"^[\w\.\-]+:\d+$", ln.strip())]
print(f"[*] 拿到 {len(ip_ports)} 个入口")
print(f"[*] 入口 IP 去重: {len(set(p.split(':')[0] for p in ip_ports))} 个独立入口主机\n")

# 测前 10 个，看出口 IP 分布
exits = []
print(f"{'入口':<26} → 出口 IP")
print("-" * 50)
for ipp in ip_ports[:10]:
    try:
        r = httpx.get("https://api.ipify.org", proxy=f"http://{ipp}",
                       timeout=15.0, verify=False)
        exit_ip = r.text.strip()
        exits.append(exit_ip)
        print(f"{ipp:<26} → {exit_ip}")
    except Exception as e:
        print(f"{ipp:<26} → 失败 ({type(e).__name__})")

print()
ctr = Counter(exits)
print(f"[*] 测了 {len(exits)} 个入口，出口 IP 分布：")
for ip, n in ctr.most_common():
    print(f"    {ip}  ×{n}")
print()
print(f"[*] 独立出口 IP 个数: {len(ctr)}")
if len(ctr) <= 2:
    print(f"\n>>> 这家代理就是出口共享型，30 个入口背后只有 {len(ctr)} 个真出口 IP")
    print(">>> 对 starrailawards 按 IP 限制的投票场景完全不可用 <<<")
elif len(exits) and len(ctr) >= len(exits) * 0.8:
    print("\n>>> 入口和出口基本 1:1，这家代理是独立出口型，能用 <<<")
else:
    print(f"\n>>> 入口 {len(exits)} → 出口 {len(ctr)}，部分共享，对投票场景效果有限")
