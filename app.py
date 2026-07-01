# -*- coding: utf-8 -*-
"""
懂鸟图鉴 Flask 版
数据来源：bird_files/Aboutbirds/Bird Packs/<鸟类编号>/

功能：
  1. 图鉴：中文 / 拼音 / 英文 / 学名搜索，显示图片、音频、说明
  2. 图片测试：随机出图，四选一
  3. 声音测试：随机出音频，四选一
  三个功能均可按“地点”筛选范围。

地点配置见 locations.json（首次启动自动生成，可手动编辑）。
"""
import json
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor

from flask import (Flask, abort, jsonify, render_template, request,
                   send_from_directory, url_for)

# ---------------------------------------------------------------- 路径配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'bird_files', 'Aboutbirds', 'Bird Packs')
LOCATION_FILE = os.path.join(BASE_DIR, 'locations.json')

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 300
app.config['TEMPLATES_AUTO_RELOAD'] = True


@app.template_filter('birddesc')
def birddesc(text):
    """转换鸟类说明里的自定义标签为 HTML。
       <bird,ID,名>  -> 站内链接
       <b,名>        -> 加粗
       <i,名>        -> 斜体（学名）
    """
    if not text:
        return ''
    text = re.sub(r'<bird,(\d+),([^>]+)>',
                  r'<a class="bird-link" href="/bird/\1">\2</a>', text)
    text = re.sub(r'<b,([^>]+)>', r'<b>\1</b>', text)
    text = re.sub(r'<i,([^>]+)>', r'<i>\1</i>', text)
    return text

# ---------------------------------------------------------------- 中国省份 / 大区
# 用于从鸟类 JSON 的 “地理分布” 文本中自动解析其分布省份
PROVINCES = [
    # 直辖市
    '北京', '天津', '上海', '重庆',
    # 省
    '河北', '山西', '辽宁', '吉林', '黑龙江', '江苏', '浙江', '安徽',
    '福建', '江西', '山东', '河南', '湖北', '湖南', '广东', '海南',
    '四川', '贵州', '云南', '陕西', '甘肃', '青海', '台湾',
    # 自治区
    '内蒙古', '广西', '西藏', '宁夏', '新疆',
    # 特别行政区
    '香港', '澳门',
    # 大区
    '东北', '华北', '华东', '华中', '华南', '西南', '西北', '东南',
]

# ---------------------------------------------------------------- 全局缓存
BIRDS = {}          # id(int) -> 轻量索引 dict
LOCATIONS = {}      # 地点名 -> [id, ...]  或 None 表示“全部”

# ---------------------------------------------------------------- 拼音支持
try:
    from pypinyin import Style, lazy_pinyin
    _HAS_PINYIN = True

    def _pinyin_of(text):
        full = ''.join(lazy_pinyin(text))
        abbr = ''.join(lazy_pinyin(text, style=Style.FIRST_LETTER))
        return full.lower(), abbr.lower()
except ImportError:
    _HAS_PINYIN = False

    def _pinyin_of(text):
        return '', ''


# ---------------------------------------------------------------- 数据加载
def _parse_provinces(geo_text):
    """从“地理分布”文本中提取出现的省份 / 大区。"""
    if not geo_text:
        return []
    return [p for p in PROVINCES if p in geo_text]


def _build_index(bid_str, data):
    """从单个鸟类原始 JSON 构建轻量索引。"""
    bid = data.get('id')
    try:
        bid = int(bid)
    except (TypeError, ValueError):
        bid = int(bid_str)

    name = data.get('name', '') or ''
    props = data.get('properties', {}) or {}
    name_en = props.get('英文名', '') or ''
    sci = props.get('拉丁学名', '') or ''

    images = data.get('images', []) or []
    sounds = data.get('sounds', []) or []
    image_list = [im.get('webp') for im in images if im.get('webp')]
    sound_list = [sd.get('mp3') for sd in sounds if sd.get('mp3')]

    full_py, abbr = _pinyin_of(name)

    cnwiki = data.get('cnwiki', {}) or {}
    desc = cnwiki.get('desc', {}) or {}
    geo = desc.get('地理分布', '') or ''
    provinces = _parse_provinces(geo)

    return {
        'id': bid,
        'name': name,
        'name_en': name_en,
        'sci': sci,
        'pinyin': full_py,
        'abbr': abbr,
        'image': image_list[0] if image_list else '',
        'sound': sound_list[0] if sound_list else '',
        'images': image_list,
        'sounds': sound_list,
        'image_count': len(image_list),
        'sound_count': len(sound_list),
        'provinces': provinces,
    }


def _load_one(bid_str):
    jpath = os.path.join(DATA_DIR, bid_str, f'{bid_str}.json')
    if not os.path.exists(jpath):
        return None
    try:
        with open(jpath, encoding='utf-8') as f:
            return _build_index(bid_str, json.load(f))
    except Exception as e:
        print(f'[警告] 读取 {bid_str} 失败: {e}')
        return None


def load_all_birds():
    """多线程加载所有鸟类，构建索引。"""
    if not os.path.isdir(DATA_DIR):
        raise RuntimeError(f'数据目录不存在: {DATA_DIR}')

    dirs = [d for d in os.listdir(DATA_DIR)
            if os.path.isdir(os.path.join(DATA_DIR, d))]
    print(f'正在加载 {len(dirs)} 个鸟类数据 ...')
    with ThreadPoolExecutor(max_workers=8) as ex:
        for item in ex.map(_load_one, dirs):
            if item:
                BIRDS[item['id']] = item
    print(f'加载完成，共 {len(BIRDS)} 种鸟类。')

    if not _HAS_PINYIN:
        print('提示：未安装 pypinyin，拼音搜索不可用（中文搜索仍可用）。'
              '可执行 pip install pypinyin 启用。')


# ---------------------------------------------------------------- 地点配置
def save_locations():
    out = {
        '_说明': ('在此自定义地点。格式："地点名": [鸟类id]。'
                '"_开头"的键会被忽略；"全部": null 代表所有鸟类。'
                '可按需删除自动生成的省份 / 大区，仅保留你关心的地点。'),
        **LOCATIONS,
    }
    with open(LOCATION_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def location_choices():
    """供页面下拉框使用的地点名列表（忽略以 _ 开头的说明字段）。"""
    return [k for k in LOCATIONS.keys() if not k.startswith('_')]


def ensure_locations():
    """加载或自动生成 locations.json。

    JSON 格式：
        {
          "全部": null,              # null 表示包含所有鸟类（不可删）
          "北京": [10000, 10001],    # 自定义地点：鸟类 id 列表
          "我的收藏": [1011, 1024]
        }
    首次运行会按“地理分布”自动生成各省 / 大区分类，用户可自由编辑。
    """
    global LOCATIONS

    existing = {}
    if os.path.exists(LOCATION_FILE):
        try:
            with open(LOCATION_FILE, encoding='utf-8') as f:
                existing = json.load(f)
        except Exception as e:
            print(f'[警告] locations.json 解析失败: {e}，将重新生成。')

    user_locs = {k: v for k, v in existing.items()
                 if k != '全部' and not k.startswith('_')}

    # 没有任何自定义地点时，按地理分布自动生成省份 / 大区
    if not user_locs:
        province_map = {}
        for bid, b in BIRDS.items():
            for p in b['provinces']:
                province_map.setdefault(p, []).append(bid)
        user_locs = {p: sorted(ids) for p, ids in province_map.items()}
        save_needed = True
    else:
        save_needed = False

    # 归一化：置顶“全部”，id 转 int 并过滤无效项
    LOCATIONS = {'全部': None}
    for k, v in user_locs.items():
        if v is None:
            LOCATIONS[k] = None
        elif isinstance(v, list):
            cleaned = []
            for i in v:
                try:
                    ii = int(i)
                except (TypeError, ValueError):
                    continue
                if ii in BIRDS and ii not in cleaned:
                    cleaned.append(ii)
            LOCATIONS[k] = cleaned
        else:
            LOCATIONS[k] = v

    if save_needed:
        save_locations()
        print(f'已自动生成 locations.json（含 {len(LOCATIONS) - 1} 个地点）。')


def birds_for_location(location):
    """返回某个地点下所有可用鸟类 id 列表。"""
    if not location or location not in LOCATIONS:
        return list(BIRDS.keys())
    ids = LOCATIONS[location]
    if ids is None:                      # “全部”
        return list(BIRDS.keys())
    return [i for i in ids if i in BIRDS]


# ---------------------------------------------------------------- 路由：页面
@app.route('/')
def index():
    return render_template('index.html',
                           locations=location_choices(),
                           total=len(BIRDS))


@app.route('/dex')
def dex():
    q = (request.args.get('q') or '').strip()
    location = request.args.get('location') or '全部'
    pool = birds_for_location(location)
    results = []

    if q:
        ql = q.lower()
        for bid in pool:
            b = BIRDS[bid]
            if (q in b['name'] or
                    ql in b['pinyin'] or
                    ql in b['abbr'] or
                    ql in b['name_en'].lower() or
                    ql in b['sci'].lower() or
                    q == str(b['id'])):
                results.append(b)
    else:
        # 无搜索词：展示该地点（+月份）下的全部鸟类
        results = [BIRDS[bid] for bid in pool]
    results.sort(key=lambda x: x['name'])

    # 友好显示地点（含月份后缀时加“X月”）
    m = re.match(r'^(.*)_(\d+)$', location)
    if m and m.group(1) in LOCATIONS:
        loc_label = f"{m.group(1).replace('/', ' › ')}（{m.group(2)}月）"
    else:
        loc_label = location.replace('/', ' › ')

    return render_template('dex.html',
                           q=q, location=location, loc_label=loc_label,
                           locations=location_choices(),
                           results=results,
                           total=len(results))


@app.route('/bird/<int:bid>')
def detail(bid):
    b = BIRDS.get(bid)
    if b is None:
        abort(404)
    jpath = os.path.join(DATA_DIR, str(bid), f'{bid}.json')
    with open(jpath, encoding='utf-8') as f:
        data = json.load(f)
    desc = (data.get('cnwiki') or {}).get('desc', {}) or {}
    props = data.get('properties', {}) or {}
    images = [im for im in (data.get('images') or []) if im.get('webp')]
    sounds = [sd for sd in (data.get('sounds') or []) if sd.get('mp3')]
    # from=quiz/image 或 quiz/sound 时，详情页显示"下一题"返回按钮
    from_quiz = request.args.get('from', '')
    return render_template('detail.html', b=b, desc=desc, props=props,
                           images=images, sounds=sounds, from_quiz=from_quiz)


@app.route('/quiz/image')
def quiz_image():
    return render_template('quiz_image.html',
                           locations=location_choices())


@app.route('/quiz/sound')
def quiz_sound():
    return render_template('quiz_sound.html',
                           locations=location_choices())


# ---------------------------------------------------------------- 路由：媒体
@app.route('/img/<int:bid>/<path:filename>')
def media_img(bid, filename):
    folder = os.path.join(DATA_DIR, str(bid), 'images')
    if not os.path.isdir(folder):
        abort(404)
    return send_from_directory(folder, filename)


@app.route('/snd/<int:bid>/<path:filename>')
def media_snd(bid, filename):
    folder = os.path.join(DATA_DIR, str(bid), 'sounds')
    if not os.path.isdir(folder):
        abort(404)
    return send_from_directory(folder, filename)


# ---------------------------------------------------------------- 路由：API
@app.route('/api/quiz')
def api_quiz():
    kind = request.args.get('kind', 'image')
    location = request.args.get('location') or '全部'
    pool = birds_for_location(location)

    if kind == 'image':
        candidates = [i for i in pool if BIRDS[i]['image']]
    else:
        candidates = [i for i in pool if BIRDS[i]['sound']]

    if len(candidates) < 4:
        return jsonify({'error': f'当前地点可选鸟类不足 4 种（仅 {len(candidates)} 种），请更换地点。'}), 400

    chosen = random.sample(candidates, 4)
    answer = random.choice(chosen)

    # 从该鸟的全部图片 / 音频中随机挑一个，避免每次都是同一张
    if kind == 'image':
        fname = random.choice(BIRDS[answer]['images'])
        media = url_for('media_img', bid=answer, filename=fname)
    else:
        fname = random.choice(BIRDS[answer]['sounds'])
        media = url_for('media_snd', bid=answer, filename=fname)

    options = [{'id': i, 'name': BIRDS[i]['name']} for i in chosen]
    random.shuffle(options)

    return jsonify({
        'media': media,
        'answer_id': answer,
        'answer_name': BIRDS[answer]['name'],
        'options': options,
    })


@app.route('/api/bird/<int:bid>')
def api_bird(bid):
    b = BIRDS.get(bid)
    if b is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(b)


# ---------------------------------------------------------------- 启动
def _init():
    load_all_birds()
    ensure_locations()


_init()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
