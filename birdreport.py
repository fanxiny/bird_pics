# -*- coding: utf-8 -*-
"""
从中国观鸟记录中心 (birdreport.cn) 获取某地区鸟类记录，
匹配本地鸟类数据后写入 locations.json。

用法：
  python birdreport.py "北京市" "北京市" ""
  python birdreport.py "广东省" "深圳市" ""
  python birdreport.py "四川省" "" ""
  python birdreport.py "浙江省" "杭州市" "余杭区" "G3"

参数：province city district [version]
  version: G3(年报3.0,默认) | CH4(第四版) | (空字符串=全部名录)
"""
import hashlib
import json
import os
import sys
import time
import uuid
import urllib.parse
from collections import OrderedDict

import requests
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA as CryptoRSA
from Crypto.Util.Padding import pad, unpad

BASE_URL = "https://api.birdreport.cn"
TAXON_URL = f"{BASE_URL}/front/record/activity/taxon"

RSA_PUB_KEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCvxXa98E1uWXnBzXkS2yHUfnBM6"
    "n3PCwLdfIox03T91joBvjtoDqiQ5x3tTOfpHs3LtiqMMEafls6b0YWtgB1dse1W5m+Fp"
    "eusVkCOkQxB4SZDH6tuerIknnmB/Hsq5wgEkIvO5Pff9biig6AyoAkdWpSek/1/B7zYI"
    "epYY0lxKQIDAQAB"
)

AES_KEY = b"C8EB5514AF5ADDB94B2207B08C66601C"  # 32 bytes -> AES-256
AES_IV = b"55DD79C6F04E1A67"                   # 16 bytes


# ---- RSA encryption ----

_rsa_key = CryptoRSA.import_key(
    __import__("base64").b64decode(RSA_PUB_KEY_B64)
)
_rsa_cipher = PKCS1_v1_5.new(_rsa_key)
_RSA_MAX_CHUNK = (_rsa_key.size_in_bytes() - 11)  # 117


def rsa_encrypt_long(text: str) -> str:
    """RSA encrypt long text by splitting into UTF-8 byte chunks."""
    import base64 as _b64
    raw = text.encode("utf-8")
    chunks = []
    offset = 0
    while offset < len(raw):
        chunk = raw[offset : offset + _RSA_MAX_CHUNK]
        chunks.append(_rsa_cipher.encrypt(chunk))
        offset += _RSA_MAX_CHUNK
    return _b64.b64encode(b"".join(chunks)).decode()


# ---- AES decryption ----

def aes_decrypt(ciphertext: str) -> str:
    raw = __import__("base64").b64decode(ciphertext)
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    decrypted = unpad(cipher.decrypt(raw), AES.block_size)
    return decrypted.decode("utf-8")


# ---- format / sign helpers ----

def data_to_json(url_encoded: str) -> dict:
    """Parse URL-encoded string into dict."""
    d = {}
    for pair in url_encoded.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            d[k] = v
    return d


def sort_ascii(d: dict) -> OrderedDict:
    """Sort dict keys in ASCII order."""
    return OrderedDict(sorted(d.items(), key=lambda x: x[0]))


def format_data(url_encoded: str) -> str:
    """format: parse -> sort keys -> JSON.stringify."""
    d = data_to_json(url_encoded)
    sorted_d = sort_ascii(d)
    return json.dumps(sorted_d, separators=(",", ":"), ensure_ascii=False)


def md5_sign(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def get_uuid() -> str:
    return uuid.uuid4().hex


def build_headers_and_body(url_encoded: str):
    fmt = format_data(url_encoded)
    encrypted = rsa_encrypt_long(fmt)
    timestamp = int(time.time() * 1000)
    request_id = get_uuid()
    sign = md5_sign(fmt + request_id + str(timestamp))
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.birdreport.cn",
        "Referer": "https://www.birdreport.cn/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
        "requestId": request_id,
        "timestamp": str(timestamp),
        "sign": sign,
    }
    return headers, encrypted


# ---- API calls ----

def _request_with_retry(method, url, data, headers, max_retries=3):
    """带退避的请求，遇到 505/403 时等待后重试。"""
    for attempt in range(max_retries):
        resp = requests.request(method, url, data=data, headers=headers, timeout=15)
        rj = resp.json()
        code = rj.get("code")
        if code in (200, 0):
            return resp
        if code == 505 or resp.status_code == 403:
            wait = 60 * (attempt + 1)
            print(f"  被限流(505)，等待 {wait} 秒后重试 ({attempt+1}/{max_retries})...")
            time.sleep(wait)
            continue
        return resp
    return resp


def get_species_via_taxon_endpoint(province, city, district, version="G3"):
    """通过 taxon 接口一次性获取某地区全部鸟种记录。

    正确接口为 /front/record/activity/taxon，传入 province/city/district
    参数即可在一次请求中返回该地区所有鸟种，无需逐条活动轮询。

    version: 名录版本，与网站一致。
      "G3"  = 中国观鸟年报-中国鸟类名录3.0版（网站默认，与导出 xlsx 一致）
      "CH4" = 中国鸟类分类与分布名录(第四版)
      ""    = 不限版本（返回两个名录的并集，数量更多）
    """
    params = {
        "page": "1",
        "limit": "1500",
        "province": province,
        "city": city,
        "district": district,
        "version": version,
    }
    url_encoded = urllib.parse.urlencode(params)
    headers, body = build_headers_and_body(url_encoded)
    print("  请求 taxon 接口...")
    resp = _request_with_retry("POST", TAXON_URL, body, headers)
    rj = resp.json()
    code = rj.get("code")
    if code not in (200, 0):
        print(f"  API 错误: code={code} msg={rj.get('msg')}")
        return set()
    encrypted_data = rj.get("data", "")
    if not encrypted_data:
        return set()
    taxa = json.loads(aes_decrypt(encrypted_data))
    print(f"  接口返回 {len(taxa)} 条鸟种记录")

    species = set()
    for t in taxa:
        name = (t.get("taxonname") or "").strip()
        if name:
            species.add(name)
        latin = (t.get("latinname") or "").strip()
        if latin:
            species.add(latin)
    return species


# ---- 匹配本地鸟类数据 ----

def load_local_birds(data_dir):
    """加载本地鸟类数据，返回 {中文名: id, 拉丁学名: id} 的映射。"""
    if not os.path.isdir(data_dir):
        raise RuntimeError(f"数据目录不存在: {data_dir}")

    name_to_id = {}
    sci_to_id = {}

    dirs = [d for d in os.listdir(data_dir)
            if os.path.isdir(os.path.join(data_dir, d))]
    for bid_str in dirs:
        jpath = os.path.join(data_dir, bid_str, f"{bid_str}.json")
        if not os.path.exists(jpath):
            continue
        try:
            with open(jpath, encoding="utf-8") as f:
                data = json.load(f)
            bid = int(data.get("id", bid_str))
            name = (data.get("name", "") or "").strip()
            props = data.get("properties", {}) or {}
            sci = (props.get("拉丁学名", "") or "").strip()
            if name:
                name_to_id[name] = bid
            if sci:
                sci_to_id[sci] = bid
        except Exception:
            continue

    return name_to_id, sci_to_id


def match_species(species_names, name_to_id, sci_to_id):
    """匹配远程鸟种名称到本地鸟类 ID。"""
    matched = {}  # id -> name
    unmatched = []

    for name in species_names:
        if name in name_to_id:
            bid = name_to_id[name]
            if bid not in matched:
                matched[bid] = name
        elif name in sci_to_id:
            bid = sci_to_id[name]
            if bid not in matched:
                matched[bid] = name
        else:
            unmatched.append(name)

    return matched, unmatched


# ---- 写入 locations.json ----

def write_to_locations(loc_name, bird_ids, location_file):
    """将鸟种 ID 列表写入 locations.json。"""
    existing = {}
    if os.path.exists(location_file):
        with open(location_file, "r", encoding="utf-8") as f:
            existing = json.load(f)

    existing[loc_name] = sorted(bird_ids)

    with open(location_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


# ---- 主流程 ----

def _strip_suffix(name):
    """去掉行政区划后缀，使分级路径更简洁：浙江省->浙江, 杭州市->杭州。"""
    if not name:
        return ''
    for suf in ('自治区', '特别行政区', '省', '市', '区', '县'):
        if name.endswith(suf) and len(name) > len(suf):
            return name[:-len(suf)]
    return name

def main():
    # Windows 控制台默认 GBK，部分鸟名生僻字（如鸂）无法输出导致崩溃
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    province = sys.argv[1]
    city = sys.argv[2] if len(sys.argv) > 2 else ""
    district = sys.argv[3] if len(sys.argv) > 3 else ""
    version = sys.argv[4] if len(sys.argv) > 4 else "G3"

    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_dir, "bird_files", "Aboutbirds", "Bird Packs")
    location_file = os.path.join(base_dir, "locations.json")

    region_name = district or city or province
    # 分级路径：浙江/杭州/余杭，前端据此把地点按省>市>区树状展示
    path_parts = [_strip_suffix(x) for x in (province, city, district) if x]
    region_path = '/'.join(path_parts) if path_parts else region_name
    ver_name = {"G3": "年报3.0", "CH4": "第四版", "": "全部名录"}.get(version, version)
    print(f"=== 从 birdreport.cn 获取 [{region_name}] 的鸟种记录（名录: {ver_name}）===")

    # 1. 加载本地鸟类数据
    print("加载本地鸟类数据...")
    name_to_id, sci_to_id = load_local_birds(data_dir)
    print(f"  本地共 {len(name_to_id)} 种鸟类")

    # 2. 从远程获取鸟种
    print(f"\n获取 [{region_name}] 的观鸟记录...")
    species = get_species_via_taxon_endpoint(province, city, district, version=version)
    print(f"\n共获取到 {len(species)} 个鸟种名称")

    # 3. 匹配本地数据
    print("\n匹配本地鸟类数据...")
    matched, unmatched = match_species(species, name_to_id, sci_to_id)
    print(f"  匹配成功: {len(matched)} 种")
    if unmatched:
        print(f"  未匹配: {len(unmatched)} 种，示例: {unmatched[:10]}")

    # 4. 写入 locations.json（用分级路径作为键，如 浙江/杭州/余杭）
    if matched:
        print(f"\n将 {len(matched)} 个鸟种 ID 写入 locations.json [{region_path}]")
        write_to_locations(region_path, list(matched.keys()), location_file)
        print("完成！")
    else:
        print("\n没有匹配到任何鸟种，未写入。")


if __name__ == "__main__":
    main()
