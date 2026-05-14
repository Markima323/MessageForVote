"""一次性代理诊断：
1) 调 proxy_api_url，看返回的原始内容（确认 API 是否通、返回格式）
2) 取一个返回的 ip:port，通过它访问 starrailawards.com，看是不是真能用
3) 根据结果反推：白名单未设 / 账密缺失 / 路由黑洞 / 还是其它

不修改任何项目文件。"""
import sys, re, time
import httpx
import yaml
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.normpath(os.path.join(HERE, "..", "reconstructed", "config.yaml"))

with open(CFG_FILE, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}

api_url = cfg.get("proxy_api_url", "")
protocol = cfg.get("proxy_protocol", "http")
print(f"[CFG] proxy_api_url = {api_url}")
print(f"[CFG] proxy_protocol = {protocol}")
print()

# --- Step 1: 拉一批 IP ---
print("[STEP 1] 调代理 API ...")
try:
    with httpx.Client(timeout=10.0) as c:
        r = c.get(api_url)
        print(f"[STEP 1] HTTP {r.status_code}, body bytes={len(r.content)}")
        body = r.text
except Exception as e:
    print(f"[STEP 1] FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

print("---- 原始返回（前 800 字符） ----")
print(body[:800])
print("---- 结束 ----\n")

# 解析出 ip:port 列表（模仿原脚本逻辑）
lines = [ln.strip() for ln in body.strip().splitlines() if ":" in ln]
ip_ports = []
for ln in lines:
    # 接受 "1.2.3.4:8080" 或 "user:pass@1.2.3.4:8080"
    if re.match(r"^[\w\.\-]+:\d+$", ln) or re.match(r"^[^\s]+:[^\s]+@[\w\.\-]+:\d+$", ln):
        ip_ports.append(ln)
print(f"[STEP 1] 解析出 {len(ip_ports)} 个 ip:port")
if not ip_ports:
    print("[STEP 1] !!! 没解析出任何 ip:port —— API 返回里可能是 JSON / 错误消息")
    print("       请把上面 '原始返回' 截图贴给我。")
    sys.exit(1)
print(f"[STEP 1] 示例: {ip_ports[0]}")
has_auth = "@" in ip_ports[0]
print(f"[STEP 1] 是否带账密 (user:pass@ip:port): {has_auth}\n")

# --- Step 2: 用这个代理访问目标网站 ---
test_url = "https://www.starrailawards.com/Vote2026/index.html"
test_ipport = ip_ports[0]
proxy_url = f"{protocol}://{test_ipport}"
print(f"[STEP 2] 通过代理 {proxy_url} 访问 {test_url}")
try:
    t0 = time.time()
    with httpx.Client(
        proxy=proxy_url,
        timeout=15.0,
        verify=False,  # 代理 MITM 可能换证书，先放过 TLS 错误，专注看认证
        follow_redirects=False,
    ) as c:
        r = c.get(test_url)
        dt = time.time() - t0
        print(f"[STEP 2] HTTP {r.status_code}, 用时 {dt:.2f}s, body bytes={len(r.content)}")
        print(f"[STEP 2] 前 200 字符: {r.text[:200]!r}")
        print("\n[结论] 代理本身能用 —— 不是代理认证问题。")
        print("      问题更可能在 Playwright 的代理参数没传对，或目标网站本身的反爬。")
except httpx.ProxyError as e:
    print(f"[STEP 2] ProxyError: {e}")
    msg = str(e).lower()
    if "407" in msg or "auth" in msg:
        print("\n[结论] 代理需要认证（HTTP 407）。神龙后台多半没把你本机 IP 加白名单，")
        print("      或者你的套餐是账密模式而 API 没在返回里带上 user:pass。")
        print("      → 去神龙后台 https://www.shenlongproxy.com/ 检查：")
        print("        a) '终端 IP 授权' / '白名单' 里有没有你本机当前公网 IP")
        print("        b) 套餐说明里写的是 'IP 白名单' 还是 '账密认证'")
    else:
        print("\n[结论] 代理 TCP 都连不上 —— IP 可能已失效，或防火墙阻断。")
except Exception as e:
    print(f"[STEP 2] {type(e).__name__}: {e}")
    print("\n[结论] 异常类型不像认证错误，需要看具体 traceback。")
