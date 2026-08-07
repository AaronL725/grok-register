# -*- coding: utf-8 -*-
import re
from curl_cffi import requests

proxy = "http://127.0.0.1:7893"
proxies = {"http": proxy, "https": proxy}
s = requests.Session()
html = s.get(
    "https://accounts.x.ai/sign-up?redirect=grok-com",
    impersonate="chrome131",
    proxies=proxies,
    timeout=30,
).text
chunks = re.findall(r"/_next/static/chunks/[^\"']+\.js", html)
print("chunks", len(chunks))
for c in chunks:
    u = "https://accounts.x.ai" + c
    try:
        t = s.get(u, impersonate="chrome131", proxies=proxies, timeout=20).text or ""
    except Exception:
        continue
    if not any(k in t for k in ("createUserAndSession", "emailValidationCode", "castleRequestToken")):
        continue
    print("HIT", c, len(t))
    for m in re.finditer(r"createServerReference\)?\((['\"])([a-f0-9]{40,})\1", t):
        print(" CSR", m.group(2))
    # (hash, "xxx") patterns
    for m in re.finditer(r"(['\"])([a-f0-9]{40,64})\1\s*,\s*(['\"])([^'\"]{0,80})\3", t):
        label = m.group(4)
        if any(x in label.lower() for x in ("user", "signup", "sign", "session", "email", "create")):
            print(" LAB", m.group(2), label)
    # around createUserAndSession
    i = t.find("createUserAndSession")
    if i >= 0:
        print(" CTX", t[max(0, i - 120) : i + 160].replace("\n", " ")[:300])
