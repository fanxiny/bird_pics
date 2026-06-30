# -*- coding: utf-8 -*-
"""
从中国观鸟记录中心 (birdreport.cn) 获取某地区鸟类记录，
匹配本地鸟类数据后写入 locations.json。

用法：
  python birdreport.py "北京市" "北京市" ""
  python birdreport.py "广东省" "深圳市" ""
  python birdreport.py "四川省" "" ""

参数：province city district（空字符串表示不指定）
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
SEARCH_URL = f"{BASE_URL}/front/record/activity/search"

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
    return json.dumps(sorted_d, separators=(",", ":"))


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
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "http://www.birdreport.cn",
        "Referer": "http://www.birdreport.cn/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
        "requestId": request_id,
        "timestamp": str(timestamp),
        "sign": sign,
    }
    return headers, encrypted


# ---- API calls ----

def search_activities(province, city, district, page=1, limit=50,
                     start_time="", end_time=""):
    params = {
        "page": str(page),
        "limit": str(limit),
        "province": province,
        "city": city,
        "district": district,
    }
    url_encoded = urllib.parse.urlencode(params)
    headers, body = build_headers_and_body(url_encoded)
    resp = requests.post(SEARCH_URL, data=body, headers=headers, timeout=30)
    resp.raise_for_status()
    rj = resp.json()
    if rj.get("code") != 200 and rj.get("code") != 0:
        print(f"  API 错误: code={rj.get('code')} msg={rj.get('msg')}")
        return []
    encrypted_data = rj.get("data", "")
    if not encrypted_data:
        return []
    decrypted = aes_decrypt(encrypted_data)
    return json.loads(decrypted)


def get_all_species(province, city, district):
    return get_species_via_taxon_endpoint(province, city, district)


def get_species_via_taxon_endpoint(province, city, district):
    """通过搜索活动列表 -> 获取每条活动的 taxon 列表来收集鸟种。"""
    species = set()
    page = 1
    taxon_url = f"{BASE_URL}/front/activity/taxon"

    while True:
        print(f"  搜索活动第 {page} 页...")
        items = search_activities(province, city, district, page=page, limit=50)
        if not items:
            break

        for item in items:
            report_id = item.get("reportId")
            if not report_id:
                continue

            try:
                taxon_params = f"page=1&limit=50&reportId={report_id}"
                headers, body = build_headers_and_body(taxon_params)
                resp = requests.post(taxon_url, data=body, headers=headers, timeout=15)
                resp.raise_for_status()
                rj = resp.json()
                encrypted_data = rj.get("data", "")
                if encrypted_data:
                    taxa = json.loads(aes_decrypt(encrypted_data))
                    for t in taxa:
                        name = t.get("taxon_name", "").strip()
                        if name:
                            species.add(name)
                        latin = t.get("latinname", "").strip()
                        if latin:
                            species.add(latin)
            except Exception as e:
                print(f"  获取活动 {report_id} 鸟种失败: {e}")

            time.sleep(0.15)

        page += 1
        if page > 50:
            break

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

def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    province = sys.argv[1]
    city = sys.argv[2] if len(sys.argv) > 2 else ""
    district = sys.argv[3] if len(sys.argv) > 3 else ""

    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_dir, "bird_files", "Aboutbirds", "Bird Packs")
    location_file = os.path.join(base_dir, "locations.json")

    region_name = district or city or province
    print(f"=== 从 birdreport.cn 获取 [{region_name}] 的鸟种记录 ===")

    # 1. 加载本地鸟类数据
    print("加载本地鸟类数据...")
    name_to_id, sci_to_id = load_local_birds(data_dir)
    print(f"  本地共 {len(name_to_id)} 种鸟类")

    # 2. 从远程获取鸟种
    print(f"\n获取 [{region_name}] 的观鸟记录...")
    species = get_species_via_taxon_endpoint(province, city, district)
    print(f"\n共获取到 {len(species)} 个鸟种名称")

    # 3. 匹配本地数据
    print("\n匹配本地鸟类数据...")
    matched, unmatched = match_species(species, name_to_id, sci_to_id)
    print(f"  匹配成功: {len(matched)} 种")
    if unmatched:
        print(f"  未匹配: {len(unmatched)} 种，示例: {unmatched[:10]}")

    # 4. 写入 locations.json
    if matched:
        print(f"\n将 {len(matched)} 个鸟种 ID 写入 locations.json [{region_name}]")
        write_to_locations(region_name, list(matched.keys()), location_file)
        print("完成！")
    else:
        print("\n没有匹配到任何鸟种，未写入。")


if __name__ == "__main__":
    main()
