# -*- coding: utf-8 -*-
"""Flyme 固件数据维护脚本（GitHub Actions 用，不是 App 客户端）

1) 读 data/firmware.json（没有就用根目录 firmware.json 种子）
2) 合并 data/manual-urls.txt：每行 "完整链接 | 机型"
3) --scrape：抓官网 official_page 的内联 JSON，只处理当前最新版；
   跟 download 到 firmware-res.flyme.com，去掉 auth_key 还原 download.meizu.com 永久直链
4) 每次运行重建 data/unresolved-links.txt（没有未解决项时只留表头）
5) 排序并写回 data/firmware.json

用法：
  python tools/update_data.py
  python tools/update_data.py --scrape
  python tools/update_data.py --check-sort
"""
import datetime
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
FW = os.path.join(DATA_DIR, "firmware.json")
SEED = os.path.join(ROOT, "firmware.json")
MANUAL = os.path.join(DATA_DIR, "manual-urls.txt")
UNRESOLVED = os.path.join(DATA_DIR, "unresolved-links.txt")
ALIASES = os.path.join(ROOT, "tools", "model-aliases.json")

URL_RE = re.compile(
    r"/(Firmware|Dailytest)/Flyme/([^/]+)/([^/]+)/([^/]+)/((?:\d{14}(?:-\d{14})?)|(?:\d{8}(?:-\d{8})?))/([0-9A-Fa-f]{8}|[0-9A-Za-z]{6})/update\.zip", re.I)

UNRESOLVED_HEADER = ("# 机型\t版本\t发布时间\tMD5\t文件大小\tdownload 入口\t"
                     "最终 firmware-res.flyme.com 地址\t失败原因\n")

URL_ALIAS = [
    ("/Firmware/Flyme/meizu2_Pro/12.25.5.16/cn_daily/20250512191421-20250516022243/03fdf276/update.zip",
     "https://download.meizu.com/Firmware/Flyme/meizu20_Pro/12.25.5.16/cn_daily/20250512191421-20250516022243/03fdf276/update.zip"),
    ("/Firmware/Flyme/PRO7_Plus/6.1.2.3//20180111121511/736521af/update.zip",
     "https://download.meizu.com/Firmware/Flyme/PRO7_Plus/6.1.2.3/20180111121511/736521af/update.zip"),
]


def norm(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def load_aliases():
    if not os.path.exists(ALIASES):
        return {}, {}
    with io.open(ALIASES, encoding="utf-8") as f:
        j = json.load(f)
    by_name, rev = {}, {}
    for name, v in (j.get("phones") or {}).items():
        keys = [name] + list(v.get("aliases") or [])
        by_name[name] = set(norm(x) for x in keys)
        for x in keys:
            k = norm(x)
            if k in rev and rev[k] != name:
                rev[k] = None
            else:
                rev.setdefault(k, name)
    return by_name, rev


ALIAS_BY_NAME, ALIAS_REV = load_aliases()


def resolve_model(text):
    n = norm(text)
    if not n:
        return None
    if "21pro" in n and "钛好用" in n:
        return "魅族 21 PRO Flyme 钛好用版"
    if "21pro" in n:
        return "魅族 21 PRO"
    if "钛好用" in n:
        return None
    return ALIAS_REV.get(n)


def load_data():
    src = FW if os.path.exists(FW) else SEED
    if not os.path.exists(src):
        return {"schema": 1, "updated": "", "phones": {}}
    with io.open(src, encoding="utf-8") as f:
        j = json.load(f)
    if "phones" not in j:
        j = {"schema": 1, "updated": "", "phones": j}
    return j


def apply_alias(u):
    s = str(u or "").strip()
    path = s.split("://", 1)[-1]
    path = "/" + path.split("/", 1)[1] if "/" in path else path
    for bad, good in URL_ALIAS:
        if path == bad or path.replace("//", "/") == bad.replace("//", "/"):
            return good
    return s


def norm_path(u):
    p = re.sub(r"^https?://[^/]+", "", str(u or ""))
    p = p.split("?", 1)[0].split("#", 1)[0]
    return re.sub(r"/{2,}", "/", p)


def parse_url(u):
    m = URL_RE.search(apply_alias(u))
    if not m:
        return None
    seg, model, ver, region, ts_raw, h = (m.group(1), m.group(2), m.group(3),
                                          m.group(4), m.group(5), m.group(6).lower())
    ts = "" if "-" in ts_raw else ts_raw
    return {"model": model, "ver": ver, "region": region, "ts": ts, "hash": h,
            "seg": seg,
            "url": "https://download.meizu.com/%s/Flyme/%s/%s/%s/%s/%s/update.zip"
                   % (seg, model, ver, region, ts_raw, h)}


def dedupe_key(model, ts, h, ver, url):
    m = str(model or "").lower()
    if h:
        return "K1:%s|%s" % (m, str(h).lower())
    if ts:
        return "K2:%s|%s|%s" % (m, ts, ver)
    return "K3:" + norm_path(url)


def add_url(data, u, model_text=""):
    """兼容旧调用：URL -> 机型 -> 添加（manual 行带机型时传 model_text）"""
    p = parse_url(u)
    if not p:
        return "无法识别", u
    hint = resolve_model(model_text) if model_text else ""
    pid = find_phone(data["phones"], p["model"], p["ver"], p["ts"], p["hash"], u, hint)
    if pid is None:
        return "机型无法判断", p["model"]
    st, _b = add_build(data["phones"][pid], p, src="doc", kind="hidden",
                       note="官网隐藏·文档收录正式版")
    return st, "%s %s" % (p["model"], p["ver"])


def ver_key(v):
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", str(v))]


def find_phone(phones, model, ver="", ts="", h="", url="", hint=""):
    cands = []
    for pid, m in phones.items():
        cdn = m.get("cdn") or []
        if model in cdn or m.get("internal") == model or str(m.get("id")) == model:
            cands.append(pid)
    # 1) 完整构建 URL
    for pid, m in phones.items():
        for g in m.get("groups") or []:
            for b in g.get("builds") or []:
                if url and b.get("url") == url:
                    return pid
    # 2) 型号 + 时间戳 + Hash
    if ts:
        for pid in cands:
            for g in phones[pid].get("groups") or []:
                for b in g.get("builds") or []:
                    if (b.get("model") == model and b.get("ts") == ts
                            and (b.get("hash") or "").lower() == h):
                        return pid
    # 3) 来源机型（manual 行 / 官网页面）
    if hint:
        for pid, m in phones.items():
            if m.get("name") == hint:
                return pid
    # 4) 只有唯一候选才按代号
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        # 共享代号：只有明确的版本规则才允许判断，否则拒绝
        if model.lower() == "meizu21pro":
            if ver.startswith("12.7"):
                for pid in cands:
                    if phones[pid].get("name") == "魅族 21 PRO":
                        return pid
            if ver.startswith("12.") and not ver.startswith("12.7"):
                for pid in cands:
                    if phones[pid].get("name") == "魅族 21 PRO Flyme 钛好用版":
                        for g in phones[pid].get("groups") or []:
                            for b in g.get("builds") or []:
                                if b.get("ts") == ts:
                                    return pid
        return None
    return None


def head_md5(url, tries=3):
    import base64
    for i in range(tries):
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            r = urllib.request.urlopen(req, timeout=25, context=ssl._create_unverified_context())
            v = r.headers.get("Content-MD5")
            return base64.b64decode(v).hex() if v else ""
        except Exception as e:
            if getattr(e, "code", 0) in (429, 403, 503):
                time.sleep(3 * (i + 1))
                continue
            return ""
    return ""


def channel_of(region):
    if region in ("cn_daily", "cn_beta", "beta", "intl_beta"):
        return "beta", "内测版"
    if region in ("intl", "intl_stable"):
        return "global", "国际版"
    return "cn", "公开版"


def add_build(phone, p, size=0, md5="", date="", src="doc", kind=None, note=""):
    groups = phone.setdefault("groups", [])
    g = next((x for x in groups if x.get("ver") == p["ver"]), None)
    if g is None:
        g = {"ver": p["ver"], "isVariant": False, "builds": []}
        if p["region"] in ("cn_daily", "cn_beta", "beta", "intl_beta"):
            g["isBeta"] = True
        groups.append(g)
    for grp in groups:
        for b in grp.get("builds") or []:
            if (b.get("model") == p["model"] and b.get("ts") == p["ts"]
                    and (b.get("hash") or "").lower() == p["hash"]):
                return "already", b
    channel, label = channel_of(p["region"])
    if kind is None:
        kind = "beta" if channel == "beta" else "hidden"
    if not note and kind == "hidden":
        note = "官网隐藏·文档收录正式版"
    code = ""
    for b in g.get("builds") or []:
        if b.get("code"):
            code = b["code"]
            break
    b = {
        "phone": int(phone.get("id") or 0), "model": p["model"], "ver": p["ver"],
        "ts": p["ts"], "hash": p["hash"], "size": size, "code": code,
        "md5": md5 or head_md5(p["url"]), "releaseDate": date,
        "kind": kind, "url": p["url"], "region": p["region"], "src": src,
        "isDiff": False, "note": note, "dead": False, "channel": channel,
        "channelLabel": label, "suf": "A" if p["region"] in ("cn", "cn_stable") else "",
    }
    g.setdefault("builds", []).append(b)
    g["builds"].sort(key=lambda x: str(x.get("ts", "")), reverse=True)
    return "added", b


def sort_groups(phone):
    groups = phone.get("groups") or []
    normal = [g for g in groups if not g.get("isBeta") and not g.get("isVariant")]
    other = [g for g in groups if g.get("isBeta") or g.get("isVariant")]
    normal.sort(key=lambda g: ver_key(g.get("ver")))
    other.sort(key=lambda g: ver_key(g.get("ver")))
    phone["groups"] = normal + other


def manual_rows():
    rows = []
    if not os.path.exists(MANUAL):
        return rows
    with io.open(MANUAL, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            model_text = ""
            if "|" in line:
                line, model_text = [x.strip() for x in line.rsplit("|", 1)]
            rows.append((line, model_text))
    return rows


def write_unresolved(rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    with io.open(UNRESOLVED, "w", encoding="utf-8", newline="\n") as f:
        f.write(UNRESOLVED_HEADER)
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", "replace")


def iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_dicts(v)


def page_builds(html):
    """解析官网内联 JSON，返回 [{ver,ts,hash,size,md5,date,download}]（只保留有 download 的）"""
    blobs = []
    for m in re.finditer(r"(?is)<script[^>]*>(.*?)</script>", html):
        s = m.group(1).strip()
        if "download" in s or "version" in s:
            blobs.append(s)
    for m in re.finditer(r"(?s)(?:__INITIAL_STATE__|__NUXT__|window\.__DATA__)\s*=\s*(\{.*?\})\s*;", html):
        blobs.append(m.group(1))
    out, seen = [], set()
    for s in blobs:
        try:
            j = json.loads(s)
        except Exception:
            continue
        for d in iter_dicts(j):
            ver = d.get("version") or d.get("ver") or d.get("flymeVersion")
            dl = d.get("download") or d.get("downloadUrl") or d.get("url")
            if not ver or not dl:
                continue
            if not re.search(r"\d+\.\d+\.\d+", str(ver)):
                continue
            ts = str(d.get("buildTime") or d.get("timestamp") or d.get("ts") or "")
            h = str(d.get("hash") or d.get("md5") or "")[-8:].lower()
            size = d.get("size") or d.get("fileSize") or 0
            try:
                size = int(size)
            except Exception:
                size = 0
            if size and size < 1024 * 1024:
                size = size * 1024 * 1024
            item = {
                "ver": str(ver), "ts": ts, "hash": h, "size": size,
                "md5": str(d.get("md5") or ""), "date": str(d.get("releaseDate") or d.get("date") or ""),
                "download": str(dl),
            }
            key = (item["ver"], item["ts"], item["hash"])
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def resolve_download(page_url, dl):
    url = urllib.parse.urljoin(page_url, dl)
    if "firmware-res.flyme.com" in url:
        signed = url
    else:
        html = fetch(url)
        m = re.search(r"https?://firmware-res\.flyme\.com/[^\s\"'<>]+\.zip[^\s\"'<>]*", html)
        signed = m.group(0) if m else ""
    if not signed:
        return "", url
    sp = urllib.parse.urlsplit(signed)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(sp.query, keep_blank_values=True)
         if not k.lower().startswith("auth_key")]
    perm = urllib.parse.urlunsplit(("https", "download.meizu.com", sp.path,
                                    urllib.parse.urlencode(q) if q else "", ""))
    return perm, signed


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--check-sort" in sys.argv:
        d = load_data()
        bad = []
        for pid, m in d["phones"].items():
            for g in m.get("groups", []):
                ts = [str(b.get("ts", "")) for b in g["builds"]]
                if ts != sorted(ts, reverse=True):
                    bad.append((pid, m.get("name", ""), g.get("ver", "")))
        print("组总数 %d | 排序不合规 %d" % (sum(len(m.get("groups", [])) for m in d["phones"].values()), len(bad)))
        return 1 if bad else 0

    data = load_data()
    phones = data["phones"]
    unresolved = []
    log = []

    # 1) 手动链接
    for url, model_text in manual_rows():
        p = parse_url(url)
        hint = resolve_model(model_text) if model_text else ""
        if not p:
            unresolved.append(["", "", "", "", "", url, "", "链接格式无法识别"])
            continue
        pid = find_phone(phones, p["model"], p["ver"], p["ts"], p["hash"], url, hint)
        if pid is None:
            unresolved.append([model_text or p["model"], p["ver"], "", "", "",
                               url, "", "共享代号或机型无法唯一判断"])
            continue
        st, b = add_build(phones[pid], p, size=0, md5="", date="",
                          src="doc", kind="hidden", note="官网隐藏·文档收录正式版")
        log.append("[manual] %s -> %s %s" % (st, phones[pid].get("name"), p["ver"]))

    # 2) 官网抓取（只抓当前最新版）
    if "--scrape" in sys.argv:
        for pid in sorted(phones.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
            phone = phones[pid]
            page = phone.get("official_page")
            if not page:
                continue
            try:
                html = fetch(page)
                builds = page_builds(html)
                if not builds:
                    log.append("[scrape] %s 页面无内联构建" % pid)
                    continue
                latest = max(builds, key=lambda b: ver_key(b["ver"]))["ver"]
                for it in builds:
                    if it["ver"] != latest:
                        continue
                    perm, signed = resolve_download(page, it["download"])
                    if not perm:
                        unresolved.append([phone.get("name", ""), it["ver"], it["date"],
                                           it["md5"], it["size"], it["download"], signed,
                                           "签名链接还原失败"])
                        print("[unresolved] %s %s" % (phone.get("name", ""), it["ver"]))
                        continue
                    p = parse_url(perm)
                    if not p:
                        unresolved.append([phone.get("name", ""), it["ver"], it["date"],
                                           it["md5"], it["size"], it["download"], signed,
                                           "永久直链格式无法识别"])
                        continue
                    st, b = add_build(phone, p, size=it["size"], md5=it["md5"],
                                      date=it["date"], src="doc", kind="hidden",
                                      note="官网隐藏·文档收录正式版")
                    log.append("[scrape] %s %s -> %s" % (phone.get("name", ""), p["ver"], st))
                time.sleep(0.3)
            except Exception as e:
                log.append("[scrape] %s 失败: %s" % (pid, e))

    # 3) 排序 + 去重键
    for pid in phones:
        for g in phones[pid].get("groups", []):
            g["builds"].sort(key=lambda b: str(b.get("ts", "")), reverse=True)
        sort_groups(phones[pid])

    data["schema"] = 1
    data["updated"] = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds")
    os.makedirs(DATA_DIR, exist_ok=True)
    with io.open(FW, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    write_unresolved(unresolved)
    print("\n".join(log) if log else "(no change)")
    print("written:", FW, os.path.getsize(FW), "bytes; unresolved", len(unresolved))
    return 0


if __name__ == "__main__":
    sys.exit(main())
