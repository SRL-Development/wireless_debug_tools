# -*- coding: utf-8 -*-
"""本机侧只读能力扩展（纯标准库，零外网、零发包、不改配置）。

包含四块：
  1) local_diagnostics()  —— netsh / ipconfig / 系统 WLAN 报告的只读解析与本机侧问题清单
  2) History              —— 空口扫描快照时间序列（JSONL 落盘、趋势统计、CSV 导出）
  3) report_docx()        —— 用 OOXML 直接生成离线 .docx 勘测报告，不依赖 Word / 第三方库
  4) backup_bytes()/restore_backup() —— 用户数据全量备份与安全恢复

隐私边界：绝不调用 `netsh wlan show profile name=X key=clear`，绝不读取明文密钥；
不 ping、不 traceroute、不联网、不修改任何无线或网络配置。
"""
import io
import os
import re
import json
import time
import zipfile
import subprocess
from xml.sax.saxutils import escape as _x

PLATFORM_WIN = os.name == "nt"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _now(t=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t or time.time()))


def _decode(raw):
    for enc in ("utf-8", "gbk", "cp936", "mbcs", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _run(argv, timeout=6):
    """只读执行系统命令，返回 (文本, 错误)。不弹控制台窗口。"""
    try:
        cp = subprocess.run(argv, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
        raw = (cp.stdout or b"") + (b"" if cp.returncode == 0 else (cp.stderr or b""))
        return _decode(raw), ("netsh/系统命令返回码 %d" % cp.returncode) if cp.returncode != 0 else ""
    except FileNotFoundError:
        return "", "系统组件缺失：" + str(argv[0])
    except subprocess.TimeoutExpired:
        return "", "读取超时：" + " ".join(argv[:3])
    except Exception as ex:
        return "", str(ex)


def _lines(txt):
    return [x.rstrip() for x in (txt or "").splitlines()]


def _after(line):
    """取 '键: 值' / '键：值' 中的值部分。"""
    for sep in (":", "："):
        if sep in line:
            return line.split(sep, 1)[1].strip()
    return ""


def _has(line, *words):
    low = line.lower()
    return any(w.lower() in low for w in words)


# ---------------------------------------------------------------- 1. 本机诊断
_WEAK_AUTH = ("wep", "tkip", "开放", "open", "shared", "wpa-personal", "wpa-个人")


def _parse_driver(txt):
    out = {"name": "", "vendor": "", "version": "", "date": "", "radios": [],
           "auth": [], "hosted": "", "fips": "", "raw": []}
    for line in _lines(txt):
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if _has(s, "驱动程序", "driver") and not out["name"] and ":" in s:
            out["name"] = _after(s)
        elif _has(s, "供应商", "vendor") and not out["vendor"] and ":" in s:
            out["vendor"] = _after(s)
        elif _has(s, "版本", "version") and not out["version"] and ":" in s:
            out["version"] = _after(s)
        elif _has(s, "日期", "date") and not out["date"] and ":" in s:
            out["date"] = _after(s)
        elif _has(s, "无线电类型", "radio types", "radio type"):
            v = _after(s)
            out["radios"] = [x for x in re.findall(r"802\.11[a-z]{1,4}|6\s?GHz|5\s?GHz|2\.4\s?GHz", v, re.I)]
            if not out["radios"] and v:
                out["radios"] = [v[:80]]
        elif _has(s, "承载网络", "hosted network"):
            out["hosted"] = _after(s)
        elif _has(s, "fips"):
            out["fips"] = _after(s)
        elif _has(s, "身份验证", "authentication") and ":" in s:
            v = _after(s)
            if v:
                out["auth"].append(v[:160])
        if ":" in s or "：" in s:
            out["raw"].append(s[:180])
    out["raw"] = out["raw"][:40]
    out["radios"] = list(dict.fromkeys(out["radios"]))[:12]
    return out


def _profile_names(txt):
    names = []
    for line in _lines(txt):
        if not _has(line, "所有用户配置文件", "当前用户配置文件", "all user profile", "current user profile"):
            continue
        v = _after(line).strip()
        if v and v.lower() not in ("none", "无"):
            names.append(v)
    return list(dict.fromkeys(names))[:24]


def _profile_detail(name):
    txt, err = _run(["netsh", "wlan", "show", "profile", 'name=%s' % name], timeout=4)
    d = {"name": name, "auth": "", "cipher": "", "ssid": "", "connection": "", "error": err}
    for line in _lines(txt):
        s = line.strip()
        if _has(s, "身份验证", "authentication") and ":" in s and not d["auth"]:
            d["auth"] = _after(s)
        elif (_has(s, "密码内容", "cipher", "加密", "密码") and not _has(s, "安全密钥")
              and ":" in s and not d["cipher"]):
            d["cipher"] = _after(s)
        elif _has(s, "连接模式", "connection mode") and ":" in s:
            d["connection"] = _after(s)
        elif _has(s, "ssid 名称", "ssid name") and ":" in s and not d["ssid"]:
            d["ssid"] = _after(s)
    low = ((d["auth"] or "") + " " + (d["cipher"] or "")).lower()
    weak = [w for w in ("wep", "tkip", "open", "开放", "shared", "noprotection") if w in low]
    if "wpa-personal" in low or "wpa-个人" in low or "wpa-psk" in low:
        weak.append("WPA1")
    d["weak"] = bool(weak)
    d["weak_reason"] = " / ".join(dict.fromkeys(weak))
    return d


def _parse_ipconfig(txt):
    blocks, cur = [], None
    for line in _lines(txt):
        if not line.strip():
            continue
        if line[:1] not in (" ", "\t") and (":" in line or "：" in line):
            if cur and (cur.get("ip") or cur.get("gateway")):
                blocks.append(cur)
            cur = {"adapter": line.split(":", 1)[0].split("：", 1)[0].strip(), "ip": "",
                   "mask": "", "gateway": "", "dns": [], "dhcp": "", "lease": ""}
            continue
        if cur is None:
            continue
        s = line.strip()
        low = s.lower()
        v = _after(s)
        if _has(s, "ipv4 地址", "ipv4 address", "ip 地址", "ip address") and not cur["ip"]:
            cur["ip"] = v.replace("(首选)", "").replace("(Preferred)", "").strip()
        elif _has(s, "子网掩码", "subnet mask") and not cur["mask"]:
            cur["mask"] = v
        elif _has(s, "默认网关", "default gateway") and not cur["gateway"]:
            cur["gateway"] = v
        elif _has(s, "dns 服务器", "dns servers") or "dns" in low and "suffix" not in low:
            if v and re.match(r"^[\d\.:a-fA-F%]+$", v):
                cur["dns"].append(v)
        elif _has(s, "dhcp 已启用", "dhcp enabled"):
            cur["dhcp"] = v
        elif _has(s, "获得租约", "lease obtained", "租约"):
            cur["lease"] = v
    if cur and (cur.get("ip") or cur.get("gateway")):
        blocks.append(cur)
    for b in blocks:
        b["dns"] = list(dict.fromkeys(b["dns"]))[:4]
        b["adapter"] = re.sub(r"^(以太网适配器|无线局域网适配器|Ethernet adapter|Wireless LAN adapter)\s*", "", b["adapter"]).strip()
    return blocks[:10]


_REPORT_PATHS = [
    os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                 "Microsoft", "Windows", "WlanReport", "wlan-report-latest.html"),
]


def _parse_wlan_report(path):
    out = {"available": False, "path": path, "generated": "", "events": [],
           "disconnects": 0, "failures": 0, "reasons": [], "error": ""}
    if not os.path.isfile(path):
        out["error"] = "系统尚未生成 WLAN 报告（可点「生成系统报告」触发 Windows 自带 netsh wlan show wlanreport）"
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
    except Exception as ex:
        out["error"] = "报告读取失败：" + str(ex)
        return out
    out["available"] = True
    out["generated"] = _now(os.path.getmtime(path))
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
    reasons = []
    for row in rows:
        cells = [re.sub(r"<[^>]+>", " ", c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        joined = " | ".join(cells)
        if not re.search(r"disconnect|connect|failure|success|断开|连接|失败|成功", joined, re.I):
            continue
        ev = {"time": cells[0][:32], "kind": cells[1][:40],
              "ssid": next((c[:40] for c in cells[2:] if c and not _has(c, "success", "成功")), ""),
              "detail": joined[:220]}
        out["events"].append(ev)
        if re.search(r"disconnect|断开", ev["kind"], re.I):
            out["disconnects"] += 1
        if re.search(r"fail|失败|reason|原因", joined, re.I):
            out["failures"] += 1
            for c in cells[2:]:
                if len(c) > 8 and not re.match(r"^[\d\s\-:/\.]+$", c):
                    reasons.append(c[:80])
    out["events"] = out["events"][:60]
    out["reasons"] = list(dict.fromkeys(reasons))[:8]
    return out


def local_diagnostics(with_profiles=True):
    """汇总本机只读信息。绝不读取明文密钥、不发包、不改配置。"""
    d = {"ok": True, "checked_at": _now(), "platform": os.name,
         "driver": {}, "profiles": [], "network": [], "wlan_report": {},
         "findings": [], "notes": [
             "全程只读：netsh / ipconfig 查询与系统已生成的 WLAN 报告解析；不读取任何明文无线密钥",
             "不 ping、不 traceroute、不联网、不修改无线或网络配置；所有输出只在本机浏览器呈现",
         ]}
    if not PLATFORM_WIN:
        d["ok"] = False
        d["error"] = "本机诊断依赖 Windows netsh，当前系统不支持"
        return d

    dtxt, derr = _run(["netsh", "wlan", "show", "drivers"], timeout=8)
    d["driver"] = _parse_driver(dtxt)
    d["driver"]["error"] = derr
    d["driver"]["available"] = bool(d["driver"].get("name")) and not derr

    ptxt, perr = _run(["netsh", "wlan", "show", "profiles"], timeout=8)
    names = _profile_names(ptxt)
    d["profile_error"] = perr
    if with_profiles:
        for n in names:
            d["profiles"].append(_profile_detail(n))
    else:
        d["profiles"] = [{"name": n, "weak": False, "auth": "", "cipher": ""} for n in names]

    itxt, ierr = _run(["ipconfig", "/all"], timeout=8)
    d["network"] = _parse_ipconfig(itxt)
    d["network_error"] = ierr
    d["wlan_report"] = _parse_wlan_report(_REPORT_PATHS[0])

    # ---- 本机侧问题清单（工程辅助判定，不替代抓包 / 控制器告警） ----
    f = []
    radios = " ".join(d["driver"].get("radios") or []).lower()
    if d["driver"].get("available") and "802.11ax" not in radios and "802.11be" not in radios:
        f.append(("info", "网卡未见 802.11ax / be 能力",
                  "驱动报告的无线电类型：" + (", ".join(d["driver"].get("radios") or []) or "未识别"),
                  "若需评测 Wi-Fi 6/6E/7 网络，需换用支持对应标准的网卡；本工具扫描结果仍有效但看不到 6 GHz 射频"))
    if d["driver"].get("available") and "6" not in radios and "ghz" not in radios:
        f.append(("info", "未见 6 GHz 支持", "驱动无线电类型字段未列出 6 GHz",
                  "6 GHz 频段扫描需网卡与驱动同时支持，且系统开启相应管制域"))
    weak = [p for p in d["profiles"] if p.get("weak")]
    if weak:
        f.append(("high", "本机保存了弱加密 / 旧认证配置文件",
                  "、".join("%s（%s %s）" % (p["name"], p.get("auth") or "-", p.get("weak_reason") or "") for p in weak[:5]),
                  "删除这些配置文件并重新连接；WEP / TKIP / WPA1 会拉低整体安全等级，且易被离线破解"))
    if len(d["profiles"]) >= 15:
        f.append(("low", "已保存配置文件较多", "%d 个" % len(d["profiles"]),
                  "过多的历史配置会触发后台自动探测与候选列表膨胀，建议清理不再使用的 SSID"))
    active = [b for b in d["network"] if b.get("ip") and not b["ip"].startswith("169.254")]
    if not active:
        f.append(("high", "未发现有效 IPv4 地址", "ipconfig /all 中无有效 IPv4（或只有 169.254 APIPA 地址）",
                  "检查 DHCP 是否可达、无线是否已通过认证；这是无线连接后业务不通的常见本机侧原因"))
    else:
        no_gw = [b for b in active if not b.get("gateway")]
        if no_gw:
            f.append(("medium", "适配器缺少默认网关",
                      "、".join(b["adapter"] for b in no_gw[:4]),
                      "确认 DHCP 下发网关或静态路由配置；无网关会导致跨网段业务不可达"))
        no_dns = [b for b in active if not b.get("dns")]
        if no_dns:
            f.append(("medium", "适配器未配置 DNS 服务器",
                      "、".join(b["adapter"] for b in no_dns[:4]),
                      "配置可信 DNS；DNS 缺失会表现为「能上 QQ 打不开网页」"))
    wr = d["wlan_report"]
    if wr.get("available") and wr.get("disconnects", 0) >= 5:
        f.append(("medium", "系统 WLAN 报告记录多次断开",
                  "最近报告内含 %d 条断开、%d 条失败相关记录" % (wr.get("disconnects", 0), wr.get("failures", 0)),
                  "对照「移动漫游」页的时间线核对是否为漫游失败 / 驱动重置 / 认证超时"))
    if not f:
        f.append(("info", "本机侧未发现明显配置问题",
                  "网卡、配置文件、IPv4 栈与系统 WLAN 报告均已读取",
                  "无线侧的空口质量与干扰请以「空口扫描」「现场勘测」结论为准"))
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    d["findings"] = [{"severity": a, "title": b, "evidence": c, "action": e} for a, b, c, e in
                     sorted(f, key=lambda x: order.get(x[0], 9))]
    return d


def generate_wlan_report():
    """触发 Windows 自带的 netsh wlan show wlanreport（系统写自己的报告目录），随后只读解析。"""
    if not PLATFORM_WIN:
        return {"ok": False, "error": "仅 Windows 支持"}
    txt, err = _run(["netsh", "wlan", "show", "wlanreport"], timeout=20)
    rep = _parse_wlan_report(_REPORT_PATHS[0])
    return {"ok": rep.get("available", False), "message": (txt or err or "").strip()[:300],
            "report": rep}


# ---------------------------------------------------------------- 2. 扫描历史
class History:
    """空口扫描快照时间序列；追加写 JSONL，滚动保留最近若干条。"""

    FIELDS = ["t", "at", "point", "bss_total", "radio_total", "ssid_total", "overall_score",
              "best_rssi", "avg_rssi", "avg_channel_index", "radios_24", "radios_5", "radios_6",
              "worst_channel", "open_bss", "hidden_bss", "current_ssid", "current_rssi",
              "current_channel", "utilization_max"]

    def __init__(self, user_dir, max_items=3000):
        self.path = os.path.join(user_dir, "air_history.jsonl")
        self.max_items = max_items
        os.makedirs(user_dir, exist_ok=True)

    def _read(self):
        items = []
        if not os.path.isfile(self.path):
            return items
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                        if isinstance(o, dict):
                            items.append(o)
                    except Exception:
                        pass
        except Exception:
            pass
        return items[-self.max_items:]

    def append(self, rec):
        row = {k: rec.get(k) for k in self.FIELDS}
        row["t"] = row.get("t") or time.time()
        row["at"] = row.get("at") or _now(row["t"])
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            items = self._read()
            if len(items) >= self.max_items + 200:
                self._write(items[-self.max_items:])
        except Exception:
            pass
        return row

    def _write(self, items):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def state(self, limit=400):
        items = self._read()
        view = items[-max(20, min(int(limit or 400), 3000)):]
        hours = {}
        for it in items:
            h = str(it.get("at") or "")[11:13]
            if not h.isdigit():
                continue
            b = hours.setdefault(h, {"n": 0, "score": 0.0, "index": 0.0, "radios": 0.0})
            b["n"] += 1
            b["score"] += float(it.get("overall_score") or 0)
            b["index"] += float(it.get("avg_channel_index") or 0)
            b["radios"] += float(it.get("radio_total") or 0)
        for h, b in hours.items():
            n = max(1, b["n"])
            b["score"] = round(b["score"] / n, 1)
            b["index"] = round(b["index"] / n, 1)
            b["radios"] = round(b["radios"] / n, 1)
        first, last = (items[0], items[-1]) if items else ({}, {})
        switches = 0
        prev = None
        for it in items:
            cur = it.get("current_ssid") or ""
            if prev is not None and cur and cur != prev:
                switches += 1
            if cur:
                prev = cur
        return {"ok": True, "path": self.path, "total": len(items), "items": view,
                "by_hour": hours, "switches": switches,
                "span": {"from": first.get("at"), "to": last.get("at")},
                "delta": {"score": _delta(first, last, "overall_score"),
                          "index": _delta(first, last, "avg_channel_index"),
                          "radios": _delta(first, last, "radio_total")}}

    def clear(self):
        try:
            if os.path.isfile(self.path):
                os.remove(self.path)
        except Exception:
            pass
        return {"ok": True, "total": 0, "items": [], "by_hour": {}}

    def csv_bytes(self):
        items = self._read()
        head = ["采集时间", "采集点"] + [k for k in self.FIELDS if k not in ("t", "at", "point")]
        lines = [",".join(head)]
        for it in items:
            lines.append(",".join(_csv(it.get(k)) for k in
                                  (["at", "point"] + [k for k in self.FIELDS if k not in ("t", "at", "point")])))
        return ("\ufeff" + "\n".join(lines) + "\n").encode("utf-8")


def _delta(a, b, key):
    try:
        return round(float(b.get(key) or 0) - float(a.get(key) or 0), 1)
    except Exception:
        return 0


def _csv(v):
    s = "" if v is None else str(v)
    if any(c in s for c in (',', '"', '\n')):
        return '"' + s.replace('"', '""') + '"'
    return s


def history_record(scan, connected=None):
    """把一次空口扫描结果压成一行历史记录。"""
    a = scan.get("analysis") or {}
    k = a.get("rollup") or {}
    bss = scan.get("bss") or []
    rssi = [float(b.get("rssi")) for b in bss if isinstance(b.get("rssi"), (int, float))]
    chs = a.get("channels") or []
    util = [float(c.get("utilization") or 0) for c in chs] if chs else []
    worst = k.get("worst_channel") or {}
    wc = ("%sG ch%s" % (worst.get("band"), worst.get("channel"))) if worst else ""
    cur = connected or {}
    return {
        "point": scan.get("point") or "本机所在位置",
        "bss_total": k.get("bss_total", len(bss)),
        "radio_total": k.get("radio_total"), "ssid_total": k.get("ssid_total"),
        "overall_score": k.get("overall_score"),
        "best_rssi": k.get("best_rssi"),
        "avg_rssi": round(sum(rssi) / len(rssi), 1) if rssi else None,
        "avg_channel_index": k.get("avg_channel_index"),
        "radios_24": k.get("radios_24"), "radios_5": k.get("radios_5"), "radios_6": k.get("radios_6", 0),
        "worst_channel": wc, "open_bss": k.get("open_bss"), "hidden_bss": k.get("hidden_bss"),
        "current_ssid": cur.get("ssid") or "", "current_rssi": cur.get("rssi"),
        "current_channel": cur.get("channel"),
        "utilization_max": round(max(util), 1) if util else None,
    }


# ---------------------------------------------------------------- 3. Word 报告
def _p(text="", style=None, bold=False, size=21, color=None):
    ppr = ""
    if style:
        ppr += '<w:pStyle w:val="%s"/>' % style
    ppr += '<w:spacing w:after="90"/>'
    rpr = '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>'
    if bold:
        rpr += "<w:b/>"
    if color:
        rpr += '<w:color w:val="%s"/>' % color
    rpr += '<w:sz w:val="%d"/>' % size
    return ('<w:p><w:pPr>%s</w:pPr><w:r><w:rPr>%s</w:rPr>'
            '<w:t xml:space="preserve">%s</w:t></w:r></w:p>') % (ppr, rpr, _x(str(text)))


def _cell(text, w=1800, bold=False):
    return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr>%s</w:tc>'
            % (w, _p(text, bold=bold, size=18)))


def _table(rows, widths=None, header=True):
    if not rows:
        return ""
    n = len(rows[0])
    widths = widths or [int(9000 / n)] * n
    borders = "".join('<w:%s w:val="single" w:sz="4" w:color="D0D7E2"/>' % x
                      for x in ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = ['<w:tbl><w:tblPr><w:tblW w:w="9000" w:type="dxa"/><w:tblBorders>%s</w:tblBorders></w:tblPr>' % borders]
    for ri, row in enumerate(rows):
        out.append("<w:tr>" + "".join(_cell(str(c)[:120], widths[i] if i < len(widths) else widths[-1],
                                            bold=(header and ri == 0)) for i, c in enumerate(row)) + "</w:tr>")
    out.append("</w:tbl>")
    out.append('<w:p><w:pPr><w:spacing w:after="80"/></w:pPr></w:p>')
    return "".join(out)


_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:eastAsia="Microsoft YaHei"/>'
           '<w:sz w:val="21"/></w:rPr></w:rPrDefault></w:docDefaults>'
           '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
           '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
           '<w:rPr><w:b/><w:sz w:val="34"/><w:color w:val="1D4ED8"/></w:rPr></w:style>'
           '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
           '<w:rPr><w:b/><w:sz w:val="26"/><w:color w:val="1D4ED8"/></w:rPr></w:style>'
           '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
           '<w:rPr><w:b/><w:sz w:val="22"/></w:rPr></w:style>'
           '</w:styles>')


def report_docx(ctx):
    """用 OOXML 生成单文件 .docx。ctx 由 app.py 的 _survey_context() 汇总。"""
    brand = ctx.get("brand") or {}
    ev = ctx.get("eval") or {}
    au = ctx.get("audit") or {}
    air = ctx.get("air") or {}
    roll = (au.get("rollup") or (air.get("analysis") or {}).get("rollup") or {})
    st = ev.get("stats") or {}
    stt = ctx.get("store") or {}
    title = brand.get("title") or "无线现场勘测与验收报告"
    session = stt.get("session") or "未命名勘测会话"
    parts = [_p(title, style="Title"),
             _p("Wireless Site Survey & Acceptance Report · 由 WirelessNetOptimizer 本地生成，全程离线", size=18, color="667085"),
             _p("生成时间 %s　|　软件版本 v%s" % (ctx.get("now") or _now(), ctx.get("version") or "-"), size=18)]
    if brand.get("confidential", True):
        parts.append(_p("保密文件 · 仅供项目内部与客户交付使用", bold=True, size=18, color="B45309"))
    for k, v in (("机构 / 客户", brand.get("org")), ("项目名称", brand.get("project")),
                 ("勘测会话", session), ("勘测人", brand.get("engineer")), ("复核人", brand.get("reviewer"))):
        if v:
            parts.append(_p("%s：%s" % (k, v)))

    parts.append(_p("1 · 执行摘要", style="Heading1"))
    parts.append(_table([
        ["指标", "数值", "口径"],
        ["勘测点位", ev.get("total", 0), "人工在楼层图点击固化的本机实测快照"],
        ["达标 / 边缘 / 不达标", "%s / %s / %s" % (ev.get("pass", 0), ev.get("edge", 0), ev.get("fail", 0)),
         "按验收模板逐点判定"],
        ["达标率", "%s%%" % (ev.get("pass_rate") if ev.get("pass_rate") is not None else "-"), "达标点 / 总点数"],
        ["区域评分", "%s（%s）" % (roll.get("overall_score", "-"), roll.get("overall_grade", "-")),
         "单点空口快照，不代表整层"],
        ["审计健康分", "%s（%s）" % (au.get("health", "-"), au.get("grade", "-")), "规则库扣分指数衰减"],
        ["空口 BSS / 射频", "%s / %s" % (roll.get("bss_total", "-"), roll.get("radio_total", "-")),
         "BSSID 前 5 字节 + 频段归并"],
        ["RSSI 最小 / SINR 均值", "%s / %s" % (st.get("rssi_min", "-"), st.get("sinr_avg", "-")), "来自实测点位"],
        ["问题总数", au.get("finding_total", 0), "严重 / 高 / 中 / 低分级"],
    ], [2200, 2600, 4200]))
    if au.get("top_action"):
        parts.append(_p("首要处置建议：" + au["top_action"], bold=True))

    parts.append(_p("2 · 逐点验收明细", style="Heading1"))
    pts = ev.get("items") or []
    rows = [["点位", "SSID", "坐标", "RSSI", "SINR", "干扰指数", "达标", "采集时间"]]
    for p in pts[:120]:
        rows.append([p.get("label") or "-", p.get("ssid") or "-",
                     "%s,%s" % (p.get("x") if p.get("x") is not None else "-",
                                p.get("y") if p.get("y") is not None else "-"),
                     p.get("rssi"), p.get("sinr"), p.get("interference_index"),
                     p.get("status_text") or "-", p.get("at_text") or "-"])
    if len(rows) == 1:
        parts.append(_p("本次会话尚无实测点位。"))
    else:
        parts.append(_table(rows, [1100, 1700, 1000, 900, 900, 1000, 900, 1500]))
    if len(pts) > 120:
        parts.append(_p("（表格仅列前 120 个点位，完整数据请用勘测页的 CSV 导出）", size=18, color="667085"))

    parts.append(_p("3 · 自动问题清单", style="Heading1"))
    rows = [["严重度", "编号", "问题", "证据", "处置建议"]]
    for f in (au.get("findings") or [])[:80]:
        rows.append([f.get("severity_text") or f.get("severity"), f.get("id"),
                     f.get("title"), "；".join(map(str, f.get("evidence") or [])),
                     "；".join(map(str, f.get("action") or []))])
    if len(rows) == 1:
        parts.append(_p("当前规则库未发现需要处置的问题，建议保存基线报告。"))
    else:
        parts.append(_table(rows, [900, 1000, 2000, 2700, 2400]))
    parts.append(_p(au.get("method") or "", size=17, color="667085"))

    parts.append(_p("4 · 信道 / 带宽 / 功率建议", style="Heading1"))
    plan = ctx.get("plan") or {}
    rows = [["频段", "忙信道数", "平均干扰", "带宽建议", "功率建议", "逐项建议"]]
    for b in (plan.get("bands") or [])[:12]:
        rows.append([str(b.get("band")) + "G", b.get("busy_count"), b.get("avg_index"),
                     b.get("width_advice") or "-", b.get("power_advice") or "-",
                     "；".join(map(str, b.get("advice") or []))[:100]])
    if len(rows) > 1:
        parts.append(_table(rows, [800, 1000, 1000, 2100, 2100, 2000]))
    if plan.get("assumption"):
        parts.append(_p("口径：" + plan["assumption"], size=17, color="667085"))
    if not rows or len(rows) == 1:
        parts.append(_p("本次无信道规划建议。"))

    parts.append(_p("5 · 安全与合规核查", style="Heading1"))
    sec = ctx.get("sec") or {}
    rows = [["严重度", "SSID / BSSID", "加密", "标记", "说明"]]
    for r in (sec.get("rows") or [])[:40]:
        rows.append([r.get("severity_text") or r.get("severity") or "-",
                     r.get("ssid") or r.get("bssid") or "-", r.get("security") or "-",
                     "、".join(map(str, r.get("flags") or []))[:60],
                     str(r.get("detail") or "-")[:100]])
    if len(rows) > 1:
        parts.append(_table(rows, [900, 1900, 1500, 1900, 2800]))
    smy = sec.get("summary") or {}
    if smy:
        parts.append(_p("汇总：开放 %s · 弱加密 %s · 隐藏 %s · 未登记 %s" %
                        (smy.get("open", 0), smy.get("weak", 0),
                         smy.get("hidden", 0), smy.get("rogue", 0)), size=18))

    parts.append(_p("6 · 传播模型校准", style="Heading1"))
    cal = ctx.get("calib") or {}
    if cal.get("ok"):
        parts.append(_table([["参数", "默认值", "校准值"],
                             ["路径损耗指数 n", cal.get("n_before", "-"), cal.get("n_fit", "-")],
                             ["1m 损耗 PL0 (dB)", cal.get("pl0_before", "-"), cal.get("pl0_fit", "-")],
                             ["RMSE (dB)", cal.get("rmse_before_db", "-"), cal.get("rmse_after_db", "-")]],
                            [3000, 3000, 3000]))
        parts.append(_p("改善 %s dB。%s" % (cal.get("improvement_db", "-"), cal.get("guard") or ""),
                        size=17, color="667085"))
    else:
        parts.append(_p("尚未用实测点校准传播模型，热力图使用默认 ITU-R P.1238 参数。"))

    sections = ctx.get("sections") or []
    if sections:
        parts.append(_p("7 · 多楼层 / 多会话合订", style="Heading1"))
        rows = [["会话 / 楼层", "点数", "达标", "边缘", "不达标", "达标率", "最差 RSSI", "SINR 均值"]]
        for s in sections:
            rows.append([s.get("session"), s.get("total"), s.get("pass"), s.get("edge"),
                         s.get("fail"), s.get("pass_rate"), s.get("rssi_min"), s.get("sinr_avg")])
        parts.append(_table(rows, [2100, 800, 800, 800, 900, 1000, 1200, 1400]))
        parts.append(_p("各楼层独立计算达标率，点位不跨楼层混合插值。", size=18, color="667085"))

    diff = ctx.get("diff") or {}
    if diff.get("ok"):
        parts.append(_p("8 · 优化前后对比", style="Heading1"))
        rows = [["指标", "优化前", "优化后", "变化"]]
        for x in (diff.get("metrics") or []):
            if isinstance(x, dict):
                rows.append([x.get("label") or x.get("key"), x.get("before"),
                             x.get("after"), x.get("delta")])
        if len(rows) > 1:
            parts.append(_table(rows, [3000, 2000, 2000, 2000]))
        notes = ["新增问题 %d 条、消除 %d 条、改级 %d 条" %
                 (len(diff.get("added") or []), len(diff.get("removed") or []),
                  len(diff.get("changed") or []))]
        for line in (diff.get("notes") or []):
            notes.append(str(line))
        for line in notes:
            parts.append(_p("· " + line))

    parts.append(_p("9 · 数据口径与能力边界", style="Heading1"))
    for line in (ctx.get("boundary") or []):
        parts.append(_p("· " + str(line), size=18))
    parts.append(_p("数据来源：本机无线网卡的 Windows Native WiFi BSS 缓存、netsh 当前关联状态，以及人工在楼层图上的点位标注。"
                    "全程只读，不连接、不断开、不改配置、不上传、不调用任何外网接口。", size=18))
    parts.append(_p("与商业工具的差异：Ekahau Sidekick / NetAlly AirMagnet Spectrum XT 使用专用射频前端，能输出实时 FFT "
                    "频谱并识别蓝牙 / 微波炉 / 雷达等非 Wi-Fi 干扰源；普通笔记本网卡只提供 802.11 Beacon/Probe 解析结果，"
                    "本软件不做原始频谱分析，干扰指数是可见射频数、频率区间重叠与 BSS Load 推算的工程估计。", size=18))
    parts.append(_p("其他边界：① 单点扫描只代表该时刻该位置；② 隐藏 SSID 名称空口不传输，无法还原；"
                    "③ 漫游切换耗时上限等于采样间隔，不是空口抓包级时延；④ 未接控制器 / SNMP，"
                    "无法读取真实发射功率、关联终端数与历史告警；⑤ 需求阈值是公开工程经验值，非厂商认证标准。", size=18))
    if brand.get("show_sign", True):
        parts.append(_p(""))
        parts.append(_table([["勘测人 / 日期", "复核人 / 日期", "客户确认 / 日期"], ["", "", ""]],
                            [3000, 3000, 3000], header=False))
    if brand.get("footer"):
        parts.append(_p(brand["footer"], size=17, color="667085"))
    parts.append(_p("WirelessNetOptimizer · 单机离线版 v%s · %s" % (ctx.get("version") or "-", _now()),
                    size=16, color="667085"))

    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
           '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
           '</w:body></w:document>') % "".join(parts)
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    drels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", drels)
        z.writestr("word/styles.xml", _STYLES)
    return buf.getvalue()


# ---------------------------------------------------------------- 4. 备份 / 恢复
_BACKUP_EXT = (".json", ".jsonl", ".png", ".jpg", ".jpeg", ".csv", ".txt")


def backup_bytes(user_dir):
    """把用户数据目录打包成 zip（不含临时文件 / 备份自身）。"""
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({
            "app": "WirelessNetOptimizer", "created_at": _now(),
            "kind": "user-data-backup", "version": 1}, ensure_ascii=False))
        for root, _dirs, files in os.walk(user_dir):
            for fn in files:
                if not fn.lower().endswith(_BACKUP_EXT) or fn.endswith(".tmp"):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, user_dir).replace("\\", "/")
                if rel.startswith(".."):
                    continue
                try:
                    z.write(full, rel)
                    n += 1
                except Exception:
                    pass
    return buf.getvalue(), n


def restore_backup(user_dir, data):
    """安全恢复：校验 zip 内路径，白名单扩展名，写回用户目录。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except Exception as ex:
        return {"ok": False, "error": "备份文件不是有效 zip：" + str(ex)}
    restored, skipped = [], []
    for info in z.infolist():
        name = info.filename.replace("\\", "/")
        if name == "manifest.json":
            continue
        if name.startswith("/") or ".." in name.split("/") or ":" in name.split("/")[0]:
            skipped.append(name)
            continue
        if not name.lower().endswith(_BACKUP_EXT):
            skipped.append(name)
            continue
        if info.file_size > 20 * 1024 * 1024:
            skipped.append(name)
            continue
        dest = os.path.abspath(os.path.join(user_dir, *name.split("/")))
        if not dest.startswith(os.path.abspath(user_dir) + os.sep):
            skipped.append(name)
            continue
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                out.write(src.read(20 * 1024 * 1024))
            restored.append(name)
        except Exception:
            skipped.append(name)
    return {"ok": True, "restored": restored, "skipped": skipped,
            "count": len(restored), "message": "已恢复 %d 个文件，请刷新页面使内存状态重新加载" % len(restored)}
