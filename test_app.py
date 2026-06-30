# -*- coding: utf-8 -*-
"""端到端测试：启动服务后运行此脚本验证各功能。"""
import json
import urllib.parse
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:5000"


def get(path, **params):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


tests = []

s, html = get("/")
tests.append(("首页", s, "懂鸟图鉴" in html))

s, html = get("/bird/10000")
tests.append(("详情页10000", s, "灰眉岩鹀" in html and "<audio" in html))

s, html = get("/dex", q="麻雀")
tests.append(("中文搜麻雀", s, html.count('class="name"') > 0))

s, html = get("/dex", q="hmyw")
tests.append(("拼音首字母hmyw", s, '/bird/10000"' in html))

s, html = get("/dex", q="huimei")
tests.append(("拼音全拼huimei", s, '/bird/10000"' in html))

s, html = get("/dex", q="duck")
tests.append(("英文搜duck", s, html.count('class="name"') > 0))

s, html = get("/dex", q="Emberiza")
tests.append(("学名搜Emberiza", s, html.count('class="name"') > 0))

s, body = get("/api/quiz", kind="image", location="全部")
d = json.loads(body)
tests.append(("图片测试API", s, d.get("media", "").startswith("/img/") and len(d.get("options", [])) == 4))

s, body = get("/api/quiz", kind="sound", location="全部")
d = json.loads(body)
tests.append(("声音测试API", s, d.get("media", "").startswith("/snd/") and len(d.get("options", [])) == 4))

# 选项中正确答案必须只出现一次
d_img = json.loads(get("/api/quiz", kind="image")[1])
names = [o["name"] for o in d_img["options"]]
tests.append(("四选项不重复", 200, len(names) == len(set(names)) and len(names) == 4))

# 地点筛选（云南）应返回结果或友好错误
s, body = get("/api/quiz", kind="image", location="云南")
try:
    d = json.loads(body)
    ok = ("options" in d) or ("error" in d)
except Exception:
    ok = False
tests.append(("地点筛选-云南", s, ok))

# 媒体文件可下载
req = urllib.request.Request(BASE + "/img/10000/ugc_10000_ND_20200309095255_19522689_0.webp")
with urllib.request.urlopen(req, timeout=30) as r:
    size = len(r.read())
tests.append(("图片媒体下载", r.status, size > 10000))

# 404
s, _ = get("/bird/99999999")
tests.append(("不存在鸟类404", s, s == 404))

print("-" * 50)
passed = 0
for name, status, ok in tests:
    flag = "PASS" if ok else "FAIL"
    print(f"  {flag}  {name:18s} HTTP {status}")
    if ok:
        passed += 1
print("-" * 50)
print(f"  {passed}/{len(tests)} passed")
