# -*- coding: utf-8 -*-
"""
现场勘测与智能审计引擎（对标 Ekahau Survey / NetAlly AirMagnet AirWISE 的本地实现）

设计约束（与整个软件一致）：
  * 仅 Python 标准库；零外网；所有数据落 %APPDATA%/WirelessNetOptimizer/。
  * 只读采集：数据来源是本机的 WlanGetNetworkBssList 缓存 / netsh 关联状态 / 手工录入，
    不连接、不断开、不改配置、不发送探测。
  * 明确标注能力边界：普通 Wi-Fi 网卡不提供原始 IQ / FFT，做不到真正的频谱分析仪
    （Ekahau Sidekick / AirMagnet Spectrum XT 那类），非 Wi-Fi 干扰只能做间接推断。

模块划分：
  PROFILES          需求（验收）模板，Ekahau Requirements 的本地化
  SurveyStore       勘测会话 + 点位库持久化
  capture_point()   把一次真实扫描 / 漫游快照固化成图纸上的一个点位
  idw_grid()        实测点位反距离加权插值 -> 覆盖/SINR/干扰/达标图层
  evaluate()        按需求模板逐点验收 + 区域达标率 + 整改建议
  calibrate()       用实测点拟合路径损耗指数 n 与 1m 损耗 PL0（Ekahau "模型校准"）
  audit()           AirWISE 式自动问题引擎：证据 / 影响 / 处置 / 关联处置步骤
  channel_plan()    基于实测射频的信道 / 带宽 / 功率规划建议
  security_findings() 非法 AP / 仿冒热点 / 弱加密 / 随机 MAC 审计
  report_html()     单文件离线勘测验收报告（浏览器直接打印为 PDF）
"""
import os
import json
import math
import time
import html
import hashlib

# ---------------------------------------------------------------- 需求模板
# 阈值是行业常见工程口径（Cisco VoWLAN / Ekahau Requirements / Aruba VRD 的公开推荐值），
# 不是任何厂商的认证标准；现场应结合终端类型与 SLA 调整，报告里会原样列出。
PROFILES = {
    "voice": {
        "key": "voice", "name": "语音 / VoWiFi", "icon": "☎",
        "desc": "双向实时业务，要求无缝漫游与双 AP 冗余覆盖",
        "min_rssi": -67, "min_sinr": 25, "min_ap_count": 2,
        "max_interference_index": 45, "max_utilization_pct": 50,
        "max_roam_gap_sec": 1.5, "prefer_band": "5",
        "basis": "Cisco VoWLAN / Ekahau Voice 常用口径：-67 dBm、SNR≥25 dB、每点至少 2 个 AP ≥-67 dBm",
    },
    "data": {
        "key": "data", "name": "通用办公数据", "icon": "▤",
        "desc": "网页 / 邮件 / 文件，容忍短时速率下降",
        "min_rssi": -70, "min_sinr": 20, "min_ap_count": 1,
        "max_interference_index": 60, "max_utilization_pct": 70,
        "max_roam_gap_sec": 3.0, "prefer_band": "",
        "basis": "通用企业覆盖口径：-70 dBm、SNR≥20 dB、单 AP 可达",
    },
    "hd": {
        "key": "hd", "name": "高密度 / 会场", "icon": "▦",
        "desc": "每平方米终端多，重点是容量与空口争用而非单纯信号",
        "min_rssi": -65, "min_sinr": 25, "min_ap_count": 2,
        "max_interference_index": 40, "max_utilization_pct": 45,
        "max_roam_gap_sec": 1.5, "prefer_band": "5",
        "basis": "高密 VRD 口径：小区边缘 -65 dBm、信道利用率 <50%、5G 优先、40 MHz 以下带宽",
    },
    "rtls": {
        "key": "rtls", "name": "定位 / RTLS / 物联网", "icon": "◎",
        "desc": "低速终端，要求三点以上可见以便三角定位",
        "min_rssi": -75, "min_sinr": 15, "min_ap_count": 3,
        "max_interference_index": 65, "max_utilization_pct": 75,
        "max_roam_gap_sec": 5.0, "prefer_band": "2.4",
        "basis": "BLE / Wi-Fi RTLS 常用口径：≥3 个 AP 可见、边缘 -75 dBm 即可",
    },
}
PROFILE_ORDER = ["voice", "data", "hd", "rtls"]

# 常见 OUI（BSSID 前三字节）。仅用于"疑似厂商"提示，不作为判责依据。
OUI = {
    "00:0B:85": "Cisco", "00:1B:D4": "Cisco", "00:22:BD": "Cisco",
    "00:24:C4": "Cisco Meraki", "88:15:44": "Cisco Meraki", "AC:17:C8": "Cisco",
    "00:0B:86": "Aruba / HPE", "00:1A:1E": "Aruba / HPE", "20:4C:03": "Aruba / HPE",
    "00:11:74": "Aruba", "24:DE:C6": "Aruba / HPE",
    "00:27:1C": "Ruckus", "F8:E7:1E": "Ruckus", "58:93:96": "Ruckus", "C0:8A:DE": "Ruckus",
    "00:15:6D": "Ubiquiti", "04:18:D6": "Ubiquiti", "24:A4:3C": "Ubiquiti", "78:8A:20": "Ubiquiti",
    "00:18:82": "Huawei", "00:9A:CD": "Huawei", "AC:E2:15": "Huawei", "48:DB:50": "Huawei",
    "00:0F:E2": "H3C", "3C:8C:40": "H3C", "70:F9:6D": "H3C",
    "00:E0:4C": "Ruijie", "58:69:6C": "Ruijie", "80:05:88": "Ruijie",
    "00:14:78": "TP-Link", "50:C7:BF": "TP-Link", "A4:2B:B0": "TP-Link", "30:B5:C2": "TP-Link",
    "00:0C:42": "MikroTik", "4C:5E:0C": "MikroTik",
    "00:17:F2": "Apple", "A4:83:E7": "Apple", "F0:18:98": "Apple",
    "00:16:6F": "Intel", "A0:A8:CD": "Intel",
    "00:09:0F": "Fortinet", "70:4C:A5": "Fortinet",
    "00:12:3F": "Dell", "00:1E:4F": "Extreme", "00:11:88": "Extreme",
    "00:19:77": "ZTE", "00:1D:D9": "ZTE",
    "00:1B:2F": "D-Link", "00:26:5A": "D-Link",
    "00:09:5B": "Netgear", "A0:40:A0": "Netgear",
    "00:1E:58": "Samsung", "FC:F1:36": "Samsung",
}
DFS_CHANNELS_5 = set(range(52, 141, 4))
CH24_PLAN = [1, 6, 11]


def _ts(t=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t or time.time()))


def _hm(t=None):
    return time.strftime("%H:%M:%S", time.localtime(t or time.time()))


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def vendor_of(bssid):
    """返回 (厂商名, 是否随机 MAC)。随机 MAC 的本地管理位为 1。"""
    s = str(bssid or "").upper().replace("-", ":")
    parts = s.split(":")
    if len(parts) < 2 or len(parts[0]) != 2:
        return "未知厂商", False
    try:
        local_bit = int(parts[0][1], 16) & 0x02
    except ValueError:
        return "未知厂商", False
    if local_bit:
        return "随机/私有 MAC", True
    pre = ":".join(parts[:3])
    return OUI.get(pre, "未登记 OUI"), False


def profile_of(key, custom=None):
    if key == "custom" and custom:
        p = dict(PROFILES["data"])
        for k in ("min_rssi", "min_sinr", "min_ap_count",
                  "max_interference_index", "max_utilization_pct", "max_roam_gap_sec"):
            v = _num(custom.get(k))
            if v is not None:
                p[k] = v
        p.update(key="custom", name=str(custom.get("name") or "自定义验收标准"),
                 desc="现场自定义阈值", basis="用户在本次勘测中设定的阈值，仅对当前会话有效")
        return p
    return dict(PROFILES.get(key) or PROFILES["data"])


# ---------------------------------------------------------------- 点位库
class SurveyStore:
    """勘测会话与点位。单文件 JSON，写入时先写临时文件再替换，避免半截文件。"""

    FILENAME = "survey_points.json"

    def __init__(self, user_dir):
        self.user_dir = user_dir
        self.path = os.path.join(user_dir, self.FILENAME)
        self.state = {"version": 2, "session": "", "profile": "data",
                      "custom": {}, "points": [], "floor": ""}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("points"), list):
                self.state.update(d)
        except Exception:
            pass
        return self.state

    def save(self):
        try:
            os.makedirs(self.user_dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            return True
        except Exception:
            return False

    def set_session(self, name):
        name = str(name or "").strip()[:60] or ("勘测-" + time.strftime("%m%d-%H%M"))
        self.state["session"] = name
        self.save()
        return name

    def set_profile(self, key, custom=None):
        self.state["profile"] = key if key in PROFILES or key == "custom" else "data"
        if custom:
            self.state["custom"] = dict(custom)
        self.save()
        return self.state["profile"]

    def add(self, pt):
        pt = dict(pt)
        pt.setdefault("id", hashlib.md5(
            ("%s|%s|%s|%s" % (self.state["session"], pt.get("x"), pt.get("y"), time.time())
             ).encode("utf-8")).hexdigest()[:10])
        pt.setdefault("at", time.time())
        pt.setdefault("at_text", _ts(pt["at"]))
        pt.setdefault("session", self.state["session"])
        self.state["points"].append(pt)
        self.save()
        return pt

    def delete(self, pid):
        n = len(self.state["points"])
        self.state["points"] = [p for p in self.state["points"] if str(p.get("id")) != str(pid)]
        self.save()
        return n - len(self.state["points"])

    def clear(self, session_only=True):
        if session_only and self.state["session"]:
            keep = [p for p in self.state["points"] if p.get("session") != self.state["session"]]
        else:
            keep = []
        n = len(self.state["points"]) - len(keep)
        self.state["points"] = keep
        self.save()
        return n

    def points(self, session_only=True):
        if session_only and self.state["session"]:
            return [p for p in self.state["points"] if p.get("session") == self.state["session"]]
        return list(self.state["points"])

    def sessions(self):
        seen, out = set(), []
        for p in self.state["points"]:
            s = p.get("session") or "未命名会话"
            if s not in seen:
                seen.add(s)
                out.append(s)
        if self.state["session"] and self.state["session"] not in seen:
            out.insert(0, self.state["session"])
        return out


# ---------------------------------------------------------------- 点位采集
def capture_point(req, air, roam, connected, store):
    """把一个真实快照固化成图纸点位。

    req: {x, y, label, source, rssi, sinr, ...(手工覆盖值), note}
    air: 最近一次 /api/air/last 的返回（含 analysis）；roam: 漫游快照；connected: netsh 关联
    优先级：手工覆盖 > 当前关联 > 空口扫描里同名 SSID 的最强 BSS > 全空间最强 BSS
    """
    x = _num(req.get("x"), 0.0)
    y = _num(req.get("y"), 0.0)
    ana = (air or {}).get("analysis") or {}
    roll = ana.get("rollup") or {}
    ssids = ana.get("ssids") or []
    channels = ana.get("channels") or []
    cur = (roam or {}).get("current") or connected or {}

    cur_ssid = str(cur.get("ssid") or "")
    cur_bssid = str(cur.get("bssid") or "")
    cur_rssi = _num(cur.get("rssi"))
    cur_rssi = cur_rssi if cur_rssi is not None else _num(cur.get("rssi_dbm"))
    cur_ch = _num(cur.get("channel"), None)
    cur_ch = int(cur_ch) if cur_ch is not None else None   # netsh 的信道是字符串，_num 会给 36.0，必须归一成 36
    band = str(cur.get("band") or "")

    # 优先匹配当前关联的 SSID；没有关联时取全空间最强的可见 SSID
    pick = next((s for s in ssids if s.get("ssid") and s.get("ssid") == cur_ssid), None)
    if pick is None:
        pick = next((s for s in ssids if not s.get("hidden")), None)
    if pick is None and ssids:
        pick = ssids[0]

    # 当前关联信道的干扰指数
    chan = next((c for c in channels if str(c.get("channel")) == str(cur_ch)
                 and (not band or str(c.get("band")) == band)), None)
    if chan is None and cur_ch is not None:
        chan = next((c for c in channels if str(c.get("channel")) == str(cur_ch)), None)

    bss_total = roll.get("bss_total")
    # 该点"可见射频"：RSSI ≥ -75 dBm 的 BSS 按 BSSID 前 5 字节 + 频段去重重数。
    # 不能直接用 rollup.radio_total（那是全空间归并数，不代表此处能听到几个 AP）。
    vis = set()
    for b in (air or {}).get("bss", []):
        if _num(b.get("rssi")) is None or float(b["rssi"]) < -75:
            continue
        mac = str(b.get("bssid") or "").upper().replace("-", ":").split(":")
        key = (":".join(mac[:5]) if len(mac) >= 5 else str(b.get("bssid")),
               str(b.get("band") or ""))
        vis.add(key)
    visible_radios = len(vis) if vis else None

    rssi = _num(req.get("rssi"))
    if rssi is None:
        rssi = cur_rssi
    if rssi is None:
        rssi = _num((pick or {}).get("rssi_best"), _num(roll.get("best_rssi")))
    sinr = _num(req.get("sinr"), _num((pick or {}).get("sinr_db")))
    snr = _num(req.get("snr"), _num((pick or {}).get("snr_db")))

    has_data = bool(rssi is not None or bss_total or cur_ssid)
    pt = {
        "x": round(x, 1), "y": round(y, 1),
        "label": str(req.get("label") or ("P%02d" % (len(store.points()) + 1)))[:40],
        "source": str(req.get("source") or ("scan" if bss_total else "manual")),
        "note": str(req.get("note") or "")[:200],
        "ssid": cur_ssid or (pick or {}).get("ssid") or "-",
        "bssid": cur_bssid or ((pick or {}).get("bssids") or [""])[0],
        "rssi": round(rssi, 1) if rssi is not None else None,
        "snr": round(snr, 1) if snr is not None else None,
        "sinr": round(sinr, 1) if sinr is not None else None,
        "band": band or ((pick or {}).get("bands") or [""])[0],
        "channel": int(cur_ch) if cur_ch else ((pick or {}).get("channels") or [None])[0],
        "width_mhz": ((pick or {}).get("widths") or [None])[-1],
        "security": (pick or {}).get("security") or "",
        "co_channel": (pick or {}).get("co_channel"),
        "utilization_pct": ((chan or {}).get("utilization_pct")
                            if (chan or {}).get("utilization_pct") is not None
                            else (pick or {}).get("utilization_pct")),
        "interference_index": ((chan or {}).get("interference_index")
                               if (chan or {}).get("interference_index") is not None
                               else roll.get("avg_channel_index")),
        "channel_level": (chan or {}).get("level"),
        "visible_radios": visible_radios,
        "radio_total": roll.get("radio_total"),
        "bss_total": bss_total,
        "hidden_total": roll.get("hidden_bss"),
        "open_total": roll.get("open_bss"),
        "overall_score": roll.get("overall_score"),
        "quality_score": (pick or {}).get("quality_score"),
        "scanned_at": (air or {}).get("scanned_at") or "",
        "air_age_sec": (air or {}).get("age_sec"),
        "has_data": has_data,
    }
    return store.add(pt)


# ---------------------------------------------------------------- IDW 图层
LAYERS = {
    "rssi": {"name": "信号强度 RSSI", "unit": "dBm", "min": -90, "max": -35,
             "desc": "实测点反距离加权插值；越红越弱"},
    "sinr": {"name": "信干噪比 SINR", "unit": "dB", "min": 0, "max": 40,
             "desc": "插值后的 SINR，已含同/邻频干扰"},
    "interference": {"name": "信道干扰指数", "unit": "/100", "min": 0, "max": 100,
                     "desc": "指数越高越拥挤（反向着色）"},
    "compliance": {"name": "需求达标", "unit": "", "min": 0, "max": 2,
                   "desc": "绿=达标 / 黄=边缘 / 红=不达标"},
    "ap_count": {"name": "可见射频数", "unit": "个", "min": 0, "max": 20,
                 "desc": "该点能听到的独立射频数；过多说明小区重叠，过少说明覆盖不足"},
    "utilization": {"name": "信道利用率", "unit": "%", "min": 0, "max": 100,
                    "desc": "Beacon 中 BSS Load 的信道忙时占比估计；越高越拥挤（反向着色）"},
    "snr": {"name": "信噪比 SNR", "unit": "dB", "min": 0, "max": 50,
            "desc": "仅扣热噪声，不含同频干扰；与 SINR 对比可看出干扰吃掉多少余量"},
    "sinr_gap": {"name": "干扰余量损耗", "unit": "dB", "min": 0, "max": 30,
                 "desc": "SNR − SINR：数值越大说明同/邻频干扰越严重（反向着色）"},
}


def _point_value(p, field):
    if field == "rssi":
        return _num(p.get("rssi"))
    if field == "sinr":
        return _num(p.get("sinr"), _num(p.get("snr")))
    if field == "snr":
        return _num(p.get("snr"), _num(p.get("sinr")))
    if field == "interference":
        return _num(p.get("interference_index"))
    if field == "ap_count":
        return _num(p.get("visible_radios"), _num(p.get("radio_total")))
    if field == "utilization":
        return _num(p.get("utilization_pct"))
    if field == "sinr_gap":
        snr, sinr = _num(p.get("snr")), _num(p.get("sinr"))
        return round(max(0.0, snr - sinr), 1) if snr is not None and sinr is not None else None
    if field == "compliance":
        return None
    return None


def compliance_of(p, prof):
    """返回 (status 2达标/1边缘/0不达标, reasons[], 边缘项数)。没有测量值按不可判定处理。"""
    reasons, edge = [], 0
    rssi = _num(p.get("rssi"))
    sinr = _num(p.get("sinr"), _num(p.get("snr")))
    idx = _num(p.get("interference_index"))
    apc = _num(p.get("visible_radios"), _num(p.get("radio_total")))
    util = _num(p.get("utilization_pct"))
    checked = 0
    if rssi is not None:
        checked += 1
        thr = prof["min_rssi"]
        if rssi < thr - 3:
            reasons.append("RSSI %s dBm < %s dBm" % (rssi, thr))
        elif rssi < thr:
            edge += 1
    if sinr is not None:
        checked += 1
        thr = prof["min_sinr"]
        if sinr < thr - 3:
            reasons.append("SINR %s dB < %s dB" % (sinr, thr))
        elif sinr < thr:
            edge += 1
    if idx is not None:
        checked += 1
        thr = prof["max_interference_index"]
        if idx > thr + 10:
            reasons.append("干扰指数 %s > %s" % (idx, thr))
        elif idx > thr:
            edge += 1
    if util is not None:
        checked += 1
        thr = prof["max_utilization_pct"]
        if util > thr + 10:
            reasons.append("信道利用率 %s%% > %s%%" % (util, thr))
        elif util > thr:
            edge += 1
    if apc is not None:
        checked += 1
        thr = prof["min_ap_count"]
        visible = max(1, int(apc))
        if visible < thr:
            reasons.append("可见射频 %d < %d（覆盖冗余不足）" % (visible, thr))
    if not checked:
        return 0, ["该点没有可判定的测量值"], 0
    if reasons:
        return 0, reasons, edge
    if edge:
        return 1, ["%d 项处于阈值边缘" % edge], edge
    return 2, [], 0


def evaluate(points, prof):
    """逐点验收 + 汇总 + 整改建议。"""
    items = []
    for p in (points or []):
        st, reasons, edge = compliance_of(p, prof)
        items.append({
            "id": p.get("id"), "label": p.get("label"), "x": p.get("x"), "y": p.get("y"),
            "status": st, "status_text": ["不达标", "边缘", "达标"][st],
            "reasons": reasons, "rssi": p.get("rssi"), "sinr": p.get("sinr"),
            "interference_index": p.get("interference_index"),
            "at_text": p.get("at_text"), "ssid": p.get("ssid"),
        })
    n = len(items)
    ok = sum(1 for i in items if i["status"] == 2)
    ed = sum(1 for i in items if i["status"] == 1)
    bad = n - ok - ed
    val = lambda k: [v for v in (_num(p.get(k)) for p in (points or [])) if v is not None]
    rssis, sinrs, idxs = val("rssi"), val("sinr"), val("interference_index")

    advice = []
    if bad:
        advice.append("优先复测并整改 %d 个不达标点位，按 RSSI / SINR / 干扰指数分别定位是覆盖问题还是容量问题。" % bad)
    if rssis and min(rssis) < prof["min_rssi"]:
        advice.append("存在覆盖空洞（最弱 %.0f dBm）：补点 AP、调整天线倾角或降低墙体遮挡；避免在未校准图纸上直接加功率。" % min(rssis))
    if idxs and max(idxs) > prof["max_interference_index"]:
        advice.append("存在高干扰点位（最高指数 %.0f）：先做信道重规划（2.4G 收敛到 1/6/11，5G 拆 40 MHz），再考虑降功率。" % max(idxs))
    if sinrs and min(sinrs) < prof["min_sinr"]:
        advice.append("最低 SINR %.0f dB 低于目标：检查同/邻频重叠与非 Wi-Fi 干扰源，必要时缩小小区。" % min(sinrs))
    if n and n < 5:
        advice.append("当前仅 %d 个勘测点，空间插值置信度低；建议沿走道每 5–8 米补点，并在拐角、电梯厅、会议室各取一点。" % n)
    if not advice:
        advice.append("已满足所选需求模板，建议保存本次勘测报告作为验收基线，优化后同路线复测对比。")

    return {
        "profile": prof,
        "total": n, "pass": ok, "edge": ed, "fail": bad,
        "pass_rate": round(100.0 * ok / n, 1) if n else None,
        "items": sorted(items, key=lambda i: (i["status"], -(i["rssi"] or -999))),
        "stats": {
            "rssi_min": round(min(rssis), 1) if rssis else None,
            "rssi_avg": round(sum(rssis) / len(rssis), 1) if rssis else None,
            "rssi_max": round(max(rssis), 1) if rssis else None,
            "sinr_min": round(min(sinrs), 1) if sinrs else None,
            "sinr_avg": round(sum(sinrs) / len(sinrs), 1) if sinrs else None,
            "index_max": round(max(idxs), 1) if idxs else None,
            "index_avg": round(sum(idxs) / len(idxs), 1) if idxs else None,
        },
        "advice": advice,
    }


def idw_grid(points, field, prof, cols=100, rows=70, W=1000.0, H=700.0, power=2.0):
    """反距离加权插值。返回扁平网格 + 置信度（点位密度）+ 图例统计。

    与热力图工作台的"传播模型 + 残差校正"互补：这里是纯实测驱动，不依赖 AP 布局。
    点位稀疏时置信度会明显下降，界面上用白色斜纹提示"未勘测区域"。
    """
    c = _clamp(int(cols or 100), 20, 200)
    r = _clamp(int(rows or 70), 15, 160)
    W = float(W or 1000)
    H = float(H or 700)
    grid, conf = [], []
    src = []
    for p in (points or []):
        v = _point_value(p, field) if field != "compliance" else compliance_of(p, prof)[0] * 1.0
        if v is None:
            continue
        src.append((float(p.get("x") or 0), float(p.get("y") or 0), float(v)))
    smooth = (W * H / max(1, len(src))) ** 0.5 * 0.35 if src else 1.0
    smooth = max(12.0, smooth)
    for ry in range(r):
        py = (ry + 0.5) * H / r
        for cx in range(c):
            px = (cx + 0.5) * W / c
            if not src:
                grid.append(None)
                conf.append(0.0)
                continue
            num = den = 0.0
            dmin = float("inf")
            wsum = 0.0
            for (sx, sy, sv) in src:
                d2 = (px - sx) ** 2 + (py - sy) ** 2 + smooth ** 2
                w = 1.0 / (d2 ** (power / 2.0))
                num += w * sv
                den += w
                d = math.sqrt(max(0.0, d2 - smooth ** 2))
                if d < dmin:
                    dmin = d
                wsum += w
            grid.append(round(num / den, 2))
            conf.append(round(_clamp(1.0 - dmin / (18.0 * W / 100.0), 0.0, 1.0), 2))
    vals = [v for v in grid if v is not None]
    meta = dict(LAYERS.get(field) or LAYERS["rssi"])
    meta.update(field=field, cols=c, rows=r, width=W, height=H,
                point_count=len(src), total_points=len(points or []),
                min_value=round(min(vals), 1) if vals else None,
                max_value=round(max(vals), 1) if vals else None,
                avg_value=round(sum(vals) / len(vals), 1) if vals else None,
                smooth_px=round(smooth, 1))
    if field == "compliance" and vals:
        meta["area_pass_pct"] = round(100.0 * sum(1 for v in vals if v >= 1.5) / len(vals), 1)
        meta["area_fail_pct"] = round(100.0 * sum(1 for v in vals if v < 0.5) / len(vals), 1)
    return {"grid": grid, "confidence": conf, "meta": meta}


# ---------------------------------------------------------------- 模型校准
def calibrate(points, predict_fn, n0=3.0, pl0=46.0):
    """Ekahau 式「用实测点校准传播模型」。

    predict_fn(n, pl0) -> 与 points 等长的预测 RSSI 列表（由 app.py 提供，复用现有
    ITU-R P.1238 + 多墙衰减实现，避免两套模型口径不一致）。
    网格搜索路径损耗指数 n 与 1m 参考损耗 PL0，最小化 Huber 化 RMSE；
    最后做一次留一交叉验证，防止"点少参数多"的过拟合。
    """
    meas = [_num(p.get("rssi")) for p in (points or [])]
    usable = [(i, v) for i, v in enumerate(meas) if v is not None]
    if len(usable) < 3:
        return {"ok": False, "error": "至少需要 3 个带 RSSI 的实测点才能校准传播模型",
                "point_count": len(usable)}
    if predict_fn is None:
        return {"ok": False, "error": "缺少预测模型", "point_count": len(usable)}

    def rmse_of(pred, huber=12.0):
        e = []
        for i, v in usable:
            d = pred[i] - v
            e.append(min(abs(d), huber) ** 2)
        return math.sqrt(sum(e) / len(e))

    base = predict_fn(n0, pl0)
    before = rmse_of(base) if base else None
    best = None
    ns = [2.0 + 0.1 * i for i in range(0, 31)]         # 2.0 ~ 5.0
    shifts = [-8.0 + 1.0 * i for i in range(0, 17)]    # PL0 -8 ~ +8
    for n in ns:
        for sh in shifts:
            pred = predict_fn(n, pl0 + sh)
            if not pred or len(pred) != len(meas):
                continue
            e = rmse_of(pred)
            # 轻微正则：偏离默认 n 越远越罚，避免把墙体误差全算进路径损耗
            penalty = 0.012 * abs(n - n0) + 0.004 * abs(sh)
            score = e + penalty
            if best is None or score < best[0]:
                best = (score, e, n, pl0 + sh, list(pred))
    if not best:
        return {"ok": False, "error": "预测模型未返回有效结果", "point_count": len(usable)}
    _, after, n_fit, pl0_fit, pred = best

    residuals = [round(pred[i] - v, 1) for i, v in usable]
    outliers = [{"index": i, "label": (points[i] or {}).get("label", "-"),
                 "measured": v, "predicted": round(pred[i], 1),
                 "residual": round(pred[i] - v, 1)}
                for k, (i, v) in enumerate(usable) if abs(residuals[k]) > 10]
    return {
        "ok": True, "point_count": len(usable),
        "n_before": n0, "pl0_before": pl0,
        "n_fit": round(n_fit, 2), "pl0_fit": round(pl0_fit, 1),
        "rmse_before_db": round(before, 2) if before is not None else None,
        "rmse_after_db": round(after, 2),
        "improvement_db": round(before - after, 2) if before is not None else None,
        "residual_mean_db": round(sum(residuals) / len(residuals), 2),
        "residual_abs_avg_db": round(sum(abs(x) for x in residuals) / len(residuals), 2),
        "outliers": outliers[:10], "outlier_count": len(outliers),
        "note": ("路径损耗指数 n 反映环境衰减速率：n≈2 为自由空间，n=3~3.5 为普通办公，"
                 "n>4 常见于多隔断 / 混凝土。PL0 为 1 m 参考损耗，含发射功率与天线增益的系统偏差。"
                 "校准结果只对本次点位覆盖的区域有效，换楼层需重新走测。"),
        "guard": ("若改善 < 0.5 dB 或拟合 n 落在 2.0/5.0 边界，说明点位太少或 AP 布局与图纸不符，"
                  "不建议把该参数写回预测模型。"),
    }


# ---------------------------------------------------------------- 信道规划
def channel_plan(air, ssids=None):
    ana = (air or {}).get("analysis") or {}
    channels = ana.get("channels") or []
    bss = ana.get("bss") or []
    roll = ana.get("rollup") or {}
    out = []
    for b in ("2.4", "5", "6"):
        cs = [c for c in channels if str(c.get("band")) == b]
        if not cs:
            continue
        used = [c for c in cs if (c.get("bss_on_channel") or c.get("radios_on_channel"))]
        used.sort(key=lambda c: -c.get("interference_index", 0))
        idle = sorted(cs, key=lambda c: (c.get("interference_index", 0), c.get("channel", 0)))
        if b == "2.4":
            offplan = [c["channel"] for c in used if c["channel"] not in CH24_PLAN]
            target = min(CH24_PLAN, key=lambda ch: next(
                (c["interference_index"] for c in cs if c["channel"] == ch), 0))
            advice = ("2.4G 只保留 1 / 6 / 11 三个非重叠信道；建议本次新增/调整的 AP 统一到 ch%d。"
                      "关闭 2.4G 低速速率（1/2/5.5/11 Mbps）缩小小区，发射功率建议 11–14 dBm，"
                      "与 5G 保持 6–9 dB 差值引导双频终端优先 5G。" % target)
            if offplan:
                advice = "检测到 ch%s 落在非规划信道（会与 1/6/11 邻频重叠），" % "/ch".join(map(str, offplan)) + advice
            width = "20 MHz（2.4G 禁用 40 MHz）"
            power = "11–14 dBm"
        elif b == "5":
            dfs_used = [c["channel"] for c in used if c["channel"] in DFS_CHANNELS_5]
            target = idle[0]["channel"] if idle else (36 if not dfs_used else 149)
            advice = ("5G 建议统一 40 MHz（高密会场 20 MHz），把 ch%d 作为最空闲首选。"
                      "U-NII-1（36–48）适合室内低功率，149–165 可用更高功率且避开 DFS。" % target)
            if dfs_used:
                advice += ("当前 ch%s 属 DFS 信道，遇气象/军用雷达会触发 60 s 静默并强制切信道，"
                           "语音与定位业务建议迁出。" % "/ch".join(map(str, sorted(dfs_used))))
            width = "40 MHz（低密区可 80 MHz，高密区 20 MHz）"
            power = "14–17 dBm（与 2.4G 保持 6–9 dB 差）"
        else:
            target = idle[0]["channel"] if idle else 5
            advice = "6 GHz 需 WPA3/SAE 与 6E/7 终端；优先用 AFC 允许的标准信道，保持 40–80 MHz。"
            width, power = "40–80 MHz", "按 AFC / 法规限制"
        out.append({
            "band": b, "channels": cs, "target": target,
            "worst": used[0] if used else None,
            "busy_count": len(used),
            "avg_index": round(sum(c.get("interference_index", 0) for c in cs) / len(cs), 1),
            "width_advice": width, "power_advice": power, "advice": advice,
            "register_hint": ("已登记 SSID：%s" % "、".join(x.get("name", "") for x in (ssids or [])[:6]))
                             if ssids else "",
        })
    return {"bands": out, "rollup": roll,
            "assumption": "建议基于本次单点扫描的 BSS Load / 射频数 / 频率区间重叠推算；"
                          "整层规划需多点勘测并由控制器统一做 RRM/TPC，不要逐台手工改。"}


# ---------------------------------------------------------------- 安全审计
def security_findings(air, registered_ssids=None):
    bss = ((air or {}).get("analysis") or {}).get("bss") or []
    reg = set(str(x.get("name") or "") for x in (registered_ssids or []))
    rows = []
    for b in bss:
        sec = str(b.get("security") or "未知")
        name = str(b.get("ssid") or "")
        vendor, random_mac = vendor_of(b.get("bssid"))
        flags = []
        sev = "info"
        if sec.startswith("开放") or sec in ("Open", "无加密"):
            flags.append("开放网络"); sev = "high"
        legacy = any(k in sec for k in ("WEP", "WPA1", "WPA ")) and "WPA2" not in sec and "WPA3" not in sec
        if legacy or "TKIP" in sec:
            flags.append("弱加密"); sev = "high"
        if b.get("hidden"):
            flags.append("隐藏 SSID"); sev = "medium" if sev == "info" else sev
        if reg and name and name not in reg:
            # 纯空口扫描无法判断某个 AP 是否接进了内部有线网，因此"未登记"只作核对提示，
            # 不能等同于 AirMagnet 里基于有线侧发现的 Rogue AP。
            flags.append("未登记 SSID（邻近单位 / 私接热点，需现场核对）")
        if vendor == "随机/私有 MAC":
            flags.append("随机 MAC（可能为终端热点/隐私地址）")
        if random_mac and b.get("hidden") and sec.startswith("开放"):
            flags.append("组合特征需现场确认")
        rows.append({
            "ssid": name or "（隐藏）", "bssid": b.get("bssid"), "vendor": vendor,
            "band": b.get("band"), "channel": b.get("channel"), "rssi": b.get("rssi"),
            "security": sec, "hidden": bool(b.get("hidden")),
            "flags": flags, "severity": sev if flags else "ok",
        })
    # 同名 SSID 出现多种加密 -> 仿冒热点嫌疑（AirMagnet "Evil Twin" 规则）
    byname = {}
    for r in rows:
        if r["ssid"] and r["ssid"] != "（隐藏）":
            byname.setdefault(r["ssid"], []).append(r)
    twins = []
    for name, group in byname.items():
        secs = sorted(set(g["security"] for g in group if g["security"]))
        vends = sorted(set(g["vendor"] for g in group if g["vendor"] not in ("未知厂商", "随机/私有 MAC", "未登记 OUI")))
        if len(secs) > 1 or len(vends) > 1:
            twins.append({"ssid": name, "security_set": secs, "vendor_set": vends,
                          "bss_count": len(group),
                          "detail": "同名 SSID 出现 %s 套加密配置%s，可能是仿冒热点、私接 AP 或漫游域配置不一致，需用控制器侧 BSSID 白名单核对。"
                                    % (len(secs), (" / %d 类厂商 OUI" % len(vends)) if len(vends) > 1 else "")})
            for g in group:
                if "同名异配置（疑似仿冒）" not in g["flags"]:
                    g["flags"].append("同名异配置（疑似仿冒）")
                    g["severity"] = "critical"
    rows.sort(key=lambda r: ({"critical": 0, "high": 1, "medium": 2, "info": 3, "ok": 4}[r["severity"]],
                             -(r["rssi"] or -999)))
    return {"rows": rows, "twins": twins,
            "summary": {
                "total": len(rows),
                "open": sum(1 for r in rows if "开放网络" in r["flags"]),
                "weak": sum(1 for r in rows if "弱加密" in r["flags"]),
                "hidden": sum(1 for r in rows if r["hidden"]),
                "rogue": sum(1 for r in rows if any("未登记" in f for f in r["flags"])),
                "random_mac": sum(1 for r in rows if "随机/私有 MAC" == r["vendor"]),
                "critical": sum(1 for r in rows if r["severity"] in ("critical", "high")),
            }}


# ---------------------------------------------------------------- 自动问题引擎
SEV_PENALTY = {"critical": 18, "high": 11, "medium": 6, "low": 3, "info": 0}
SEV_TEXT = {"critical": "严重", "high": "高", "medium": "中", "low": "低", "info": "提示"}


def _finding(fid, sev, cat, title, evidence, impact, action, refs=None):
    return {"id": fid, "severity": sev, "severity_text": SEV_TEXT[sev], "category": cat,
            "title": title, "evidence": evidence if isinstance(evidence, list) else [evidence],
            "impact": impact,
            "action": action if isinstance(action, list) else [action],
            "refs": refs or []}


def recompute_audit(findings, roll=None, method_suffix=""):
    """按给定问题清单重算健康分 / 等级 / 计数。

    抽成独立函数，供 audit() 与「审计规则自定义」过滤或改严重度之后复用，
    保证界面、报告、快照用的是同一个评分口径。
    """
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    out = sorted(list(findings or []), key=lambda f: (order.get(f.get("severity"), 9), str(f.get("id"))))
    penalty = sum(SEV_PENALTY.get(f.get("severity"), 0) for f in out)
    health = round(100.0 * math.exp(-penalty / 75.0))
    grade = "优" if health >= 85 else "良" if health >= 70 else "中" if health >= 55 else "差" if health >= 40 else "很差"
    counts = {k: sum(1 for f in out if f.get("severity") == k) for k in SEV_PENALTY}
    return {
        "ok": True, "generated_at": _ts(), "health": health, "grade": grade,
        "counts": counts, "finding_total": len(out), "findings": out,
        "rollup": roll or {},
        "top_action": (out[0]["action"][0] if out and out[0].get("action")
                       else "当前未发现需要处置的问题，建议保存基线报告"),
        "method": "规则库逐项比对实测 BSS / 信道 / 勘测点 / 漫游事件；健康分按严重度扣分的指数衰减计算。"
                  "这是工程辅助判定，不替代控制器侧告警与抓包复核。" + method_suffix,
    }


def _audit_tail(out, roll):
    """保留旧调用语义，统一走 recompute_audit。"""
    return recompute_audit(out, roll)


def audit(air, ssids=None, eval_result=None, roam=None, plan=None, sec=None):
    """AirWISE 式自动问题引擎：把扫描 / 勘测 / 漫游数据变成可执行的处置清单。"""
    ana = (air or {}).get("analysis") or {}
    if not ana.get("ok"):
        return {"ok": False, "error": "还没有可用的空口扫描数据，请先在「空口扫描」页扫描一次",
                "findings": [], "health": None}
    bss = ana.get("bss") or []
    roll = ana.get("rollup") or {}
    channels = ana.get("channels") or []
    ssid_rows = ana.get("ssids") or []
    reg_names = set(str(x.get("name") or "") for x in (ssids or []))
    out = []

    # --- 覆盖 / 容量 ---
    if eval_result and eval_result.get("total"):
        e = eval_result
        if e["fail"]:
            out.append(_finding(
                "COV-01", "high", "覆盖验收",
                "%d / %d 个勘测点不满足「%s」" % (e["fail"], e["total"], e["profile"]["name"]),
                ["达标率 %s%%（边缘 %d 个）" % (e["pass_rate"], e["edge"]),
                 "最弱 RSSI %s dBm，目标 %s dBm" % (e["stats"]["rssi_min"], e["profile"]["min_rssi"]),
                 "最低 SINR %s dB，目标 %s dB" % (e["stats"]["sinr_min"], e["profile"]["min_sinr"])],
                "弱覆盖区域会出现速率跌落、重传、语音断续与漫游失败。",
                ["在「现场勘测」页切到「需求达标」图层定位红色区域",
                 "先补点 AP / 调整天线方向，再考虑提高发射功率（会同时放大干扰）",
                 "整改后沿同一路线复测，对比达标率"],
                ["方案书 16.15 节 · 需求验收", "故障排查 → 弱信号症状树"]))
        if e["total"] < 5:
            out.append(_finding(
                "COV-02", "medium", "勘测完整性", "勘测点仅 %d 个，空间结论置信度不足" % e["total"],
                "点位少于 5 个时 IDW 插值基本由最近点主导，无法反映隔断与拐角衰减。",
                "可能漏掉盲区，把「局部好」误判成「整层好」。",
                "沿走道每 5–8 米补点，拐角 / 电梯厅 / 会议室 / 玻璃隔断两侧各取一点；建议单点位不少于 15 个。",
                ["方案书 16.15 节"]))
    # 只认"有 BSS 中心落在该信道"的条目。radios_on_channel 是频率区间覆盖数，
    # ch1 的 20 MHz 会合法覆盖 ch2/ch3，用它判定会把所有相邻信道误报成"非规划信道"。
    off = [c for c in channels if c.get("band") == "2.4"
           and (c.get("bss_on_channel") or 0) > 0 and c["channel"] not in CH24_PLAN]
    if off:
        out.append(_finding(
            "CH-01", "high", "信道规划", "2.4 GHz 存在 %d 个非 1/6/11 信道" % len(off),
            ["ch%s（%d 个 BSS，干扰指数 %s）" % (c["channel"], c["bss_on_channel"], c["interference_index"]) for c in off[:4]],
            "邻频重叠造成同频争用与邻道抑制，吞吐可下降 30% 以上，且无法通过加 AP 缓解。",
            ["把 2.4G 全部收敛到 1 / 6 / 11，相邻 AP 错开",
             "关闭 2.4G 的 40 MHz 与低速率集", "双频终端用 5G 优先引导（band steering / 11k/v）"],
            ["方案书 16.16 节 · 信道规划", "IEEE 802.11 2.4G 非重叠信道"]))
    dense24 = roll.get("radios_24") or 0
    if dense24 >= 5:
        out.append(_finding(
            "CH-02", "medium", "容量", "2.4 GHz 射频过密（%d 个射频 / %d 个 BSS）" % (dense24, roll.get("bss_24") or 0),
            "2.4G 最多 3 个非重叠信道，超过 4 个射频必然同频共享空口。",
            "CSMA/CA 退避加剧，时延抖动明显，低速终端会拖垮整小区。",
            ["降低 2.4G 发射功率到 11–14 dBm 缩小小区", "2.4G 只承载 IoT / 访客 / 定位，业务迁到 5G/6G",
             "开启频段引导与最低 RSSI 接入门限"], ["方案书 16.16 节"]))
    worst = roll.get("worst_channel")
    if worst and (worst.get("index") or 0) >= 55:
        better = (ana.get("recommend") or {}).get(worst["band"]) or []
        out.append(_finding(
            "CH-03", "high", "干扰", "%s GHz ch%s 干扰指数 %s（%s）" % (
                worst["band"], worst["channel"], worst["index"],
                next((c["level"] for c in channels if c["channel"] == worst["channel"]
                      and c["band"] == worst["band"]), "-")),
            ["同一频率区间内射频互相可见，AP 上报 BSS Load 被计入指数",
             "本次扫描最空闲候选：%s" % ("、".join("ch%s(指数 %s)" % (x["channel"], x["interference_index"]) for x in better[:3]) or "无")],
            "同频争用导致空口利用率饱和，表现为「信号满格但网速慢」。",
            ["由控制器统一做 RRM / DCA，避免逐台手工改造成信道震荡",
             "把该层 AP 迁到候选信道，并同步检查 80 MHz bonding 是否跨到相邻组",
             "复测同点位，确认干扰指数下降"], ["方案书 16.16 节", "空口扫描 → 信道条形图"]))
    wide = [b for b in bss if (_num(b.get("width_mhz"), 20) or 20) >= 80]
    if wide and len(bss) >= 8:
        out.append(_finding(
            "CH-04", "medium", "容量", "%d 个 BSS 使用 80 MHz 及以上带宽（共 %d 个 BSS）" % (len(wide), len(bss)),
            "80/160 MHz 把噪声底抬高 6–9 dB，且可用非重叠信道数量锐减。",
            "高密场景下 SINR 反而下降，AP 之间互相阻塞。",
            ["高密 / 会场统一 40 MHz，普通办公 40–80 MHz，只有低密独栋才用 160 MHz",
             "与控制器信道宽度模板一起下发，避免 AP 间不一致"], ["方案书 16.16 节"]))
    dfs = sorted(set(c["channel"] for c in channels if c.get("band") == "5"
                     and c.get("channel") in DFS_CHANNELS_5 and c.get("bss_on_channel")))
    if dfs:
        out.append(_finding(
            "CH-05", "medium", "合规 / 可用性", "5 GHz 正在使用 DFS 信道 ch%s" % "/ch".join(map(str, dfs)),
            "这些信道受气象 / 军用雷达保护，检测到雷达后 AP 必须 60 秒静默并切换。",
            "语音、漫游与实时定位会出现整片瞬断，且故障难以复现。",
            ["语音 / RTLS 业务优先迁到 U-NII-1（36–48）或 U-NII-3（149–165）",
             "若必须用 DFS，确认控制器已开启信道变更公告（CSA）并测试切换时长"], ["方案书 16.16 节"]))
    high_util = [s for s in ssid_rows if (_num(s.get("utilization_pct")) or 0) >= 70]
    if high_util:
        out.append(_finding(
            "CAP-01", "high", "容量", "%d 个 SSID 的 AP 上报信道利用率 ≥70%%" % len(high_util),
            ["%s：利用率 %s%%、关联终端 %s" % (s.get("label") or s.get("ssid") or "隐藏 SSID",
                                          s["utilization_pct"], s.get("stations") or "未上报")
             for s in high_util[:4]],
            "空口已接近饱和，新增终端只会排队，典型现象是「信号好、速率低、时延大」。",
            ["拆小区：降功率 + 增 AP，而不是继续加发射功率",
             "启用 5G/6G 引导、Airtime Fairness、限制低速终端",
             "检查是否有大流量广播 / 组播占用空口"], ["方案书 16.16 节"]))

    # --- Beacon 能力（v0.13：按 IE 位推断，未做关联协商验证） ---
    caps = [(b.get("cap") or {}) for b in bss]
    known = [c for c in caps if c.get("pmf") in ("required", "capable", "none")]
    no_pmf = [c for c in known if c.get("pmf") == "none"]
    if no_pmf and len(known) >= 2:
        out.append(_finding(
            "CAP-02", "medium", "协议健壮性", "%d / %d 个受保护 BSS 未启用管理帧保护（PMF）" % (
                len(no_pmf), len(known)),
            ["RSN Capabilities 中 MFPC / MFPR 均未置位，AP 未声明 802.11w 能力",
             "WPA3 与 802.11r/k/v 通常要求 PMF；此处按 Beacon IE 位判定，需在控制器侧复核"],
            "解除认证 / 解除关联帧可被伪造，造成定向断线、强制重关联与 Evil Twin 诱导。",
            ["在 SSID 安全模板中把 PMF 设为「可选」或「强制」（WPA3 必须强制）",
             "极老终端若不支持 PMF，单独建兼容 SSID 并隔离，不要拉低主 SSID"],
            ["方案书 16.20 节 · Beacon 能力矩阵", "IEEE 802.11w"]))
    by_ssid = {}
    for b in bss:
        k = str(b.get("ssid") or "")
        if k and not b.get("hidden"):
            by_ssid.setdefault(k, []).append(b)
    multi = {k: v for k, v in by_ssid.items() if len(v) >= 2}
    roam_capable = [b for b in bss if (b.get("cap") or {}).get("ft")]
    if multi and not roam_capable:
        rm_n = sum(1 for b in bss if (b.get("cap") or {}).get("rm"))
        bt_n = sum(1 for b in bss if (b.get("cap") or {}).get("btm"))
        out.append(_finding(
            "CAP-03", "medium", "漫游能力", "%d 个 SSID 有多 BSS 覆盖，但未见 802.11r 快速漫游能力" % len(multi),
            ["多 BSS SSID：%s" % "、".join(list(multi)[:5]),
             "Beacon 能力位统计：802.11r/FT %d 个、802.11k/RM %d 个、802.11v/BTM %d 个" %
             (len(roam_capable), rm_n, bt_n)],
            "语音 / 视频 / AGV 类终端跨 AP 需重新完整认证，切换耗时可达数百毫秒，易出现断续。",
            ["在控制器上为语音与关键业务 SSID 开启 802.11r（FT）、11k 邻居报告、11v BTM",
             "用「移动漫游」页沿同一路线复测，核对切换耗时与掉线事件"],
            ["方案书 16.20 节", "IEEE 802.11r / k / v"]))
    owe_n = sum(1 for c in caps if c.get("owe"))
    open_n = sum(1 for b in bss if str(b.get("security") or "").startswith(("开放", "Open")))
    if open_n and not owe_n:
        out.append(_finding(
            "SEC-06", "low", "安全", "%d 个开放 BSS 未启用 OWE 增强开放" % open_n,
            "开放网络的 Beacon 中未出现 OWE AKM（00-0F-AC:12），空口数据未做逐用户加密。",
            "同一开放网络下终端之间可被旁路监听，访客凭据与业务流量存在泄露面。",
            ["访客网络改用 OWE 或 WPA3-Enterprise，并保留客户端隔离与 Portal 认证",
             "确认不是 Captive Portal 的预期开放式设计"],
            ["方案书 16.20 节", "Wi-Fi Alliance OWE"]))
    trans = [b for b in bss if (b.get("cap") or {}).get("transition")]
    if trans:
        out.append(_finding(
            "SEC-07", "info", "安全", "%d 个 BSS 处于 WPA2/WPA3 过渡模式" % len(trans),
            "同一 BSS 的 RSN 同时宣告 SAE 与 PSK AKM（WPA3 Transition Mode）。",
            "兼容老终端的同时仍允许 WPA2-PSK 握手，无法获得 WPA3 的离线字典攻击保护。",
            ["确认老终端清单与退役计划，条件成熟后切到 WPA3-SAE 纯模式",
             "过渡期强制启用 PMF 并使用高强度预共享密钥"],
            ["方案书 16.20 节", "Wi-Fi Alliance WPA3 Transition Mode"]))

    # --- 安全 ---
    sec = sec or security_findings(air, ssids)
    smy = sec["summary"]
    if smy["open"]:
        out.append(_finding(
            "SEC-01", "high", "安全", "发现 %d 个开放 / 未加密 BSS" % smy["open"],
            ["%s（%s，ch%s，%s dBm）" % (r["ssid"], r["bssid"], r["channel"], r["rssi"])
             for r in sec["rows"] if "开放网络" in r["flags"]][:4],
            "空口可被旁路监听，且极易被同名热点仿冒；企业网络还会绕过准入控制。",
            ["确认是否为访客 Portal 的预期配置；否则改 WPA2/3-Enterprise 或至少 WPA2-PSK",
             "开放式访客网络应做客户端隔离 + 带宽限速 + Portal 认证"], ["方案书 16.16 节 · 安全审计"]))
    if smy["weak"]:
        out.append(_finding(
            "SEC-02", "critical", "安全", "存在 %d 个 WEP / WPA-TKIP 等弱加密 BSS" % smy["weak"],
            "WEP 可在数分钟内被破解，TKIP 已被 802.11 标准弃用并强制降级速率。",
            "等于没有边界防护，且会把整个 AP 的可用速率拖到 54 Mbps。",
            ["立即改 WPA2-AES 或 WPA3-SAE/Enterprise", "老终端单独建 SSID 并做 VLAN 隔离，不要拉低主 SSID"],
            ["方案书 16.16 节"]))
    if sec["twins"]:
        out.append(_finding(
            "SEC-03", "critical", "安全", "%d 个 SSID 出现同名异配置（疑似仿冒热点）" % len(sec["twins"]),
            [t["detail"] for t in sec["twins"][:3]],
            "终端可能自动关联到攻击者热点，造成凭据泄露与中间人攻击。",
            ["用控制器 BSSID 白名单逐一核对，定位私接 AP 的物理位置",
             "在 WLC / 网管侧开启 Rogue AP 检测与抑制",
             "统一同名 SSID 的安全套件与厂商配置"], ["方案书 16.16 节"]))
    if smy["rogue"] >= 3:
        out.append(_finding(
            "SEC-04", "low", "核查", "%d 个 BSS 的 SSID 不在本项目登记清单中" % smy["rogue"],
            ["登记清单：%s" % ("、".join(sorted(reg_names)) or "（空）"),
             "空口只能看到广播的 SSID，无法判断 AP 是否接入内部有线网，因此这不等同于 Rogue AP 告警"],
            "可能占用本层空口资源；若为邻近单位热点则属正常共存，无需处置。",
            ["在「空口扫描」页按信号强度排序，靠近定位信号源并与物业确认归属",
             "仅对确认接入本内网的私接 AP 才按非法 AP 流程处置"], ["方案书 16.16 节"]))
    if smy["hidden"] >= 3:
        out.append(_finding(
            "SEC-05", "low", "配置", "发现 %d 个隐藏 SSID 的 BSS" % smy["hidden"],
            "隐藏只不广播名称，Beacon / Probe 里仍能看到 BSSID、信道与加密方式。",
            "不提升安全性，反而增加终端主动探测时间与漫游延迟，还干扰信道统计。",
            ["取消隐藏，改用 802.1X / MAC 白名单 / VLAN 做准入控制",
             "确需保留时在报告中单列，避免被当成未知干扰"], ["方案书 16.16 节"]))
    if len([s for s in ssid_rows if not s.get("hidden")]) >= 12:
        out.append(_finding(
            "CFG-01", "medium", "配置", "单个空间可见 %d 个 SSID，Beacon 开销偏高" % len(ssid_rows),
            "每个 SSID 每 100 ms 发一个 Beacon，多 SSID 会持续占用空口，低速率下更明显。",
            "在 2.4G 上尤其明显，实测可吞掉 10% 以上可用空口时间。",
            ["合并功能重复的 SSID，控制在 4–6 个以内",
             "关闭 2.4G 上非必要的 SSID 广播，启用最低基本速率 12 Mbps"], ["方案书 16.16 节"]))

    # --- 漫游 ---
    if roam and roam.get("stats"):
        st = roam["stats"]
        gaps = _num(st.get("max_gap_sec") or st.get("worst_gap_sec"))
        if st.get("disconnects"):
            out.append(_finding(
                "ROAM-01", "high", "漫游", "监测期间记录到 %s 次掉线" % st["disconnects"],
                "掉线指连续两次以上轮询读不到关联；若事件标注「扫描窗口内」则可能是主动扫描导致的伪影。",
                "语音 / 视频会明显卡顿，TCP 会话可能重建。",
                ["在「移动漫游」页按时间线核对掉线发生的物理位置",
                 "检查该处是否覆盖空洞、802.11k/v/r 是否启用、认证是否超时重协商"], ["方案书 16.13 节"]))
        if st.get("sticky_events") or any("粘滞" in (i.get("title") or "") for i in (roam.get("issues") or [])):
            out.append(_finding(
                "ROAM-02", "medium", "漫游", "检测到粘滞终端（信号弱但迟迟不切换）",
                "当前关联 ≤-72 dBm 且持续 ≥20 s，同 SSID 有强 8 dB 以上的邻居却未切换。",
                "终端会一直挂在远处 AP 上，拖低自己与整个小区的速率。",
                ["启用 802.11k 邻居报告 / 802.11v BSS 过渡 / 802.11r 快速漫游",
                 "设置最低 RSSI 接入门限（-70 ~ -75 dBm）并核查终端驱动"], ["方案书 16.13 节"]))

    # 健康分与计数统一由 recompute_audit 计算；规则自定义过滤后仍复用同一口径
    return recompute_audit(out, roll)


# ---------------------------------------------------------------- 报告
_CSS = """
*{box-sizing:border-box}body{font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#1b2430;background:#eef1f6;margin:0}
.page{max-width:1000px;margin:0 auto;background:#fff;padding:44px 52px;box-shadow:0 2px 20px rgba(16,24,40,.08)}
h1{font-size:27px;margin:0 0 6px;letter-spacing:.5px}h2{font-size:18px;margin:34px 0 12px;padding-bottom:8px;border-bottom:2px solid #2563eb;color:#173a7a}
h3{font-size:14.5px;margin:18px 0 8px}.sub{color:#667085;font-size:12.5px}
.cover{border-left:6px solid #2563eb;padding:6px 0 6px 20px;margin:30px 0 26px}
.kv{display:flex;flex-wrap:wrap;gap:10px 28px;margin:18px 0;padding:16px 20px;background:#f6f8fc;border-radius:10px;font-size:12.5px}
.kv b{color:#173a7a}.cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.card{flex:1;min-width:140px;border:1px solid #e3e8f0;border-radius:10px;padding:14px 16px;background:#fff}
.card span{display:block;font-size:11.5px;color:#667085}.card b{display:block;font-size:24px;color:#173a7a;margin-top:4px}
table{width:100%;border-collapse:collapse;margin:10px 0 18px;font-size:12px}
th{background:#f2f5fb;color:#42526e;text-align:left;font-weight:650;padding:8px 9px;border-bottom:1px solid #dce3ee}
td{padding:7px 9px;border-bottom:1px solid #eef1f6;vertical-align:top}tr:nth-child(even) td{background:#fafbfd}
.tag{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;font-weight:650}
.t-ok{background:#e4f6ed;color:#0d7a52}.t-warn{background:#fff2d9;color:#96620a}
.t-bad{background:#fde9e7;color:#b42f27}.t-info{background:#eaf0fe;color:#2451b3}
.f{border:1px solid #e3e8f0;border-left:4px solid #c7372f;border-radius:8px;padding:12px 15px;margin:10px 0;page-break-inside:avoid}
.f.high{border-left-color:#e0652a}.f.medium{border-left-color:#e0a52a}.f.low,.f.info{border-left-color:#2563eb}
.f b{font-size:13.5px}.f .m{font-size:11.5px;color:#667085;margin:3px 0 7px}
.f ul{margin:5px 0 0 18px;padding:0;font-size:12px}.f li{margin:2px 0}
.note{background:#f6f8fc;border:1px solid #e3e8f0;border-radius:8px;padding:12px 16px;font-size:12px;color:#4a5768;margin:12px 0}
.ok-box{background:#eaf7f0;border:1px solid #bfe6d1;border-radius:8px;padding:14px 16px;color:#0d7a52}
footer{margin-top:40px;padding-top:14px;border-top:1px solid #e3e8f0;font-size:11px;color:#8b97a6;display:flex;justify-content:space-between}
.sign{margin-top:30px;display:flex;gap:40px;font-size:12px;color:#667085}.sign div{flex:1;border-top:1px solid #c9d2e0;padding-top:8px}
.brandbar{display:flex;align-items:center;gap:18px;border-bottom:2px solid #2563eb;padding-bottom:14px;margin-bottom:18px}
.brandbar img{max-height:58px;max-width:180px;object-fit:contain}
.brandbar .org{font-size:15px;font-weight:650;color:#173a7a}
.brandbar .proj{font-size:11.5px;color:#667085}
.conf{display:inline-block;background:#fdeceb;color:#b42f27;border:1px solid #f3c7c3;border-radius:5px;padding:1px 8px;font-size:11px;font-weight:650;margin-left:10px}
.delta-up{color:#0d7a52;font-weight:650}.delta-down{color:#b42f27;font-weight:650}.delta-flat{color:#667085}
@media print{body{background:#fff}.page{box-shadow:none;max-width:none;padding:18mm 16mm}h2{page-break-after:avoid}.f{page-break-inside:avoid}}
"""


def _e(v):
    return html.escape(str(v if v is not None else "-"), quote=True)


def _tag(text, kind="info"):
    return '<span class="tag t-%s">%s</span>' % (kind, _e(text))


def report_html(ctx):
    """生成单文件离线报告。ctx: {store, eval, audit, plan, sec, calib, air, version,
    brand, sections(多楼层合订), diff(优化前后对比)}"""
    st = ctx.get("store") or {}
    ev = ctx.get("eval") or {}
    au = ctx.get("audit") or {}
    pl = ctx.get("plan") or {}
    sc = ctx.get("sec") or {}
    cal = ctx.get("calib") or {}
    air = ctx.get("air") or {}
    br = ctx.get("brand") or {}
    sections = ctx.get("sections") or []
    diff = ctx.get("diff") or {}
    prof = ev.get("profile") or profile_of("data")
    roll = (au.get("rollup") or (air.get("analysis") or {}).get("rollup") or {})
    pts = ev.get("items") or []
    now = _ts()
    ver = ctx.get("version") or "-"
    session = _e(st.get("session") or "未命名勘测会话")
    title = _e(br.get("title") or "无线现场勘测与验收报告")
    parts = []
    A = parts.append

    A('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    A('<meta name="viewport" content="width=device-width,initial-scale=1">')
    A('<title>%s · %s</title><style>%s</style></head><body><div class="page">' % (title, session, _CSS))
    # 品牌栏：机构名 / Logo / 项目名 / 保密标记
    logo = str(br.get("logo") or "")
    if logo.startswith("data:image/"):
        logo = '<img src="%s" alt="Logo">' % _e(logo)
    else:
        logo = ""
    if logo or br.get("org") or br.get("project") or br.get("confidential"):
        A('<div class="brandbar">%s<div><div class="org">%s</div><div class="proj">%s</div></div>%s</div>' % (
            logo, _e(br.get("org") or "无线现场勘测"), _e(br.get("project") or "现场验收 / 优化对比"),
            '<span class="conf">内部资料</span>' if br.get("confidential", True) else ""))
    A('<h1>%s%s</h1>' % (title, '<span class="conf">内部资料</span>' if br.get("confidential", True)
                        and not (logo or br.get("org") or br.get("project")) else ""))
    A('<div class="sub">Wireless Site Survey &amp; Acceptance Report · 由 WirelessNetOptimizer 本地生成，全程离线</div>')
    A('<div class="cover"><div style="font-size:17px;font-weight:650">%s</div>' % session)
    A('<div class="sub">生成时间 %s　|　验收模板：%s　|　软件版本 v%s</div></div>' % (now, _e(prof["name"]), _e(ver)))
    A('<div class="kv">')
    kv = [("勘测会话", session), ("点位数量", ev.get("total", 0)),
          ("扫描时间", (air.get("scanned_at") or "-")),
          ("空口 BSS / 射频", "%s / %s" % (roll.get("bss_total", "-"), roll.get("radio_total", "-"))),
          ("区域评分", "%s（%s）" % (roll.get("overall_score", "-"), roll.get("overall_grade", "-"))),
          ("审计健康分", "%s（%s）" % (au.get("health", "-"), au.get("grade", "-")))]
    if br.get("project"):
        kv.insert(0, ("项目名称", _e(br["project"])))
    if br.get("engineer"):
        kv.append(("勘测人", _e(br["engineer"])))
    if br.get("reviewer"):
        kv.append(("复核人", _e(br["reviewer"])))
    if sections:
        kv.append(("合订范围", "%d 个楼层 / 会话" % len(sections)))
    for k, v in kv:
        A('<div><b>%s</b>　%s</div>' % (_e(k), _e(v)))
    A('</div>')

    # 执行摘要
    A('<h2>1 · 执行摘要</h2>')
    A('<div class="cards">')
    for lab, val, sub in [
        ("达标率", ("%s%%" % ev["pass_rate"]) if ev.get("pass_rate") is not None else "未勘测", "按点位判定"),
        ("达标 / 边缘 / 不达标", "%s / %s / %s" % (ev.get("pass", 0), ev.get("edge", 0), ev.get("fail", 0)), "共 %s 点" % ev.get("total", 0)),
        ("审计健康分", "%s" % au.get("health", "-"), au.get("grade", "-")),
        ("待处置问题", au.get("finding_total", 0), "严重 %s / 高 %s" % ((au.get("counts") or {}).get("critical", 0), (au.get("counts") or {}).get("high", 0))),
        ("最弱信号", ("%s dBm" % (ev.get("stats") or {}).get("rssi_min")) if (ev.get("stats") or {}).get("rssi_min") is not None else "-", "目标 %s dBm" % prof["min_rssi"]),
    ]:
        A('<div class="card"><span>%s</span><b>%s</b><span>%s</span></div>' % (_e(lab), _e(val), _e(sub)))
    A('</div>')
    A('<div class="note"><b>验收口径：</b>%s。阈值来源为公开工程经验值，不是厂商认证标准；'
      '现场应结合终端能力与业务 SLA 修订。</div>' % _e(prof.get("basis", "")))
    if au.get("top_action"):
        A('<div class="ok-box"><b>首要处置：</b>%s</div>' % _e(au["top_action"]))
    for a in (ev.get("advice") or []):
        A('<div class="note">· %s</div>' % _e(a))

    # 逐点验收
    A('<h2>2 · 逐点验收明细</h2>')
    if pts:
        A('<table><thead><tr><th>点位</th><th>坐标</th><th>SSID</th><th>RSSI</th><th>SINR</th><th>干扰指数</th><th>判定</th><th>不达标原因</th></tr></thead><tbody>')
        for i in pts:
            kind = {"2": "ok", "1": "warn", "0": "bad"}.get(str(i["status"]), "info")
            A('<tr><td>%s</td><td>(%s, %s)</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                _e(i.get("label")), _e(i.get("x")), _e(i.get("y")), _e(i.get("ssid")),
                _e(i.get("rssi")), _e(i.get("sinr")), _e(i.get("interference_index")),
                _tag(i.get("status_text"), kind), _e("；".join(i.get("reasons") or []) or "—")))
        A('</tbody></table>')
    else:
        A('<div class="note">本次没有勘测点位。请在「现场勘测」页上传/使用楼层图，沿路线点击落点采集，或使用「在当前位置打点」。</div>')

    # 自动问题
    A('<h2>3 · 自动问题清单（AirWISE 式）</h2>')
    if au.get("findings"):
        A('<div class="note">%s</div>' % _e(au.get("method", "")))
        for f in au["findings"]:
            A('<div class="f %s"><b>%s　%s</b> <span class="tag t-%s">%s</span>' % (
                f["severity"], _e(f["id"]), _e(f["title"]),
                "bad" if f["severity"] in ("critical", "high") else "warn" if f["severity"] == "medium" else "info",
                _e(f["severity_text"])))
            A('<div class="m">分类：%s</div>' % _e(f["category"]))
            A('<div style="font-size:12px"><b>证据</b><ul>%s</ul></div>' % "".join("<li>%s</li>" % _e(x) for x in f["evidence"]))
            A('<div style="font-size:12px"><b>影响：</b>%s</div>' % _e(f["impact"]))
            A('<div style="font-size:12px"><b>处置建议</b><ul>%s</ul></div>' % "".join("<li>%s</li>" % _e(x) for x in f["action"]))
            if f.get("refs"):
                A('<div class="m">关联：%s</div>' % _e("、".join(f["refs"])))
            A('</div>')
    else:
        A('<div class="ok-box">本次未触发任何审计规则。建议保存本报告作为优化前的基线。</div>')

    # 信道规划
    A('<h2>4 · 信道 / 带宽 / 功率规划建议</h2>')
    for b in (pl.get("bands") or []):
        A('<h3>%s GHz　建议首选 ch%s　%s</h3>' % (_e(b["band"]), _e(b["target"]),
                                              _tag("平均干扰指数 %s" % b["avg_index"],
                                                   "bad" if b["avg_index"] >= 55 else "warn" if b["avg_index"] >= 35 else "ok")))
        A('<div class="note">%s<br><b>带宽：</b>%s　<b>功率：</b>%s</div>' % (
            _e(b["advice"]), _e(b["width_advice"]), _e(b["power_advice"])))
        A('<div class="note" style="font-size:11.5px;padding:8px 12px">「覆盖射频」按频率区间统计，'
          '40 / 80 MHz 射频会同时覆盖多个 20 MHz 信道编号，相邻行出现同一批射频属正常。</div>')
        A('<table><thead><tr><th>信道</th><th>BSS</th><th>覆盖射频</th><th>活跃射频</th><th>利用率</th><th>干扰指数</th><th>等级</th></tr></thead><tbody>')
        for c in b["channels"]:
            A('<tr><td>ch%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                _e(c["channel"]), _e(c["bss_on_channel"]), _e(c["radios_on_channel"]),
                _e(c["active_radios"]), _e(c["utilization_pct"]),
                _e(c["interference_index"]), _e(c["level"])))
        A('</tbody></table>')
    A('<div class="note">%s</div>' % _e(pl.get("assumption", "")))

    # 安全
    A('<h2>5 · 安全与合规审计</h2>')
    smy = sc.get("summary") or {}
    A('<div class="cards">')
    for lab, val in [("BSS 总数", smy.get("total", 0)), ("开放网络", smy.get("open", 0)),
                     ("弱加密", smy.get("weak", 0)), ("未登记 SSID", smy.get("rogue", 0)),
                     ("隐藏 BSS", smy.get("hidden", 0)), ("高风险条目", smy.get("critical", 0))]:
        A('<div class="card"><span>%s</span><b>%s</b></div>' % (_e(lab), _e(val)))
    A('</div>')
    if sc.get("twins"):
        for t in sc["twins"]:
            A('<div class="note"><b>疑似仿冒：</b>%s</div>' % _e(t["detail"]))
    if sc.get("rows"):
        A('<table><thead><tr><th>SSID</th><th>BSSID</th><th>厂商（OUI 推断）</th><th>频段/信道</th><th>RSSI</th><th>加密</th><th>标记</th></tr></thead><tbody>')
        for r in sc["rows"][:60]:
            A('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s / ch%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                _e(r["ssid"]), _e(r["bssid"]), _e(r["vendor"]), _e(r["band"]),
                _e(r["channel"]), _e(r["rssi"]), _e(r["security"]),
                _e("、".join(r["flags"]) or "—")))
        A('</tbody></table>')

    # 校准
    A('<h2>6 · 传播模型校准</h2>')
    if cal.get("ok"):
        A('<div class="kv">')
        for k, v in [("实测点", cal["point_count"]), ("路径损耗指数 n", "%s → %s" % (cal["n_before"], cal["n_fit"])),
                     ("1m 参考损耗 PL0", "%s → %s dB" % (cal["pl0_before"], cal["pl0_fit"])),
                     ("留一/拟合 RMSE", "%s → %s dB（改善 %s dB）" % (cal["rmse_before_db"], cal["rmse_after_db"], cal["improvement_db"])),
                     ("平均绝对残差", "%s dB" % cal["residual_abs_avg_db"]),
                     ("异常点", cal["outlier_count"])]:
            A('<div><b>%s</b>　%s</div>' % (_e(k), _e(v)))
        A('</div><div class="note">%s<br>%s</div>' % (_e(cal["note"]), _e(cal["guard"])))
    else:
        A('<div class="note">本次未做模型校准：%s</div>' % _e(cal.get("error") or "点位不足"))

    # 边界（可在报告模板里关闭；关闭时仍会在界面口径说明中保留）
    if br.get("show_boundary", True):
        A('<h2>7 · 数据口径与能力边界</h2>')
        A('<div class="note"><b>数据来源：</b>本机无线网卡的 Windows Native WiFi BSS 缓存（SSID / BSSID / 信道 / 带宽 / '
          'RSSI / 加密 / BSS Load）、netsh 当前关联状态，以及人工在楼层图上的点位标注。全程只读，不连接、不断开、'
          '不改配置、不上传、不调用任何外网接口。<br><br>'
          '<b>与商业工具的差异（必须如实告知）：</b>Ekahau Sidekick / NetAlly AirMagnet Spectrum XT 使用专用射频前端，'
          '能输出实时 FFT 频谱、识别蓝牙 / 微波炉 / 雷达 / 无线摄像头等<b>非 Wi-Fi 干扰源</b>并做物理定位；'
          '普通笔记本网卡只提供 802.11 Beacon/Probe 解析结果，本软件<b>不做</b>原始频谱分析，'
          '报告中的干扰指数是由可见射频数、频率区间重叠与 AP 上报 BSS Load 推算的工程估计。<br><br>'
          '<b>其他边界：</b>① 单点扫描只代表该时刻该位置，整层结论需多点位采集；② 隐藏 SSID 的名称空口不传输，无法还原；'
          '③ BSSID 厂商由内置 OUI 前缀推断，随机私有 MAC 不能用于厂商判断；④ 漫游切换耗时上限等于采样间隔，不是空口抓包级时延；'
          '⑤ 主动扫描会让网卡短暂切信道，可能造成 1–2 秒瞬断；⑥ 未接控制器 / SNMP，无法读取真实发射功率、关联终端数与历史告警，'
          '相关字段均为 Beacon 中的 BSS Load 估计或留空；⑦ 噪声按 −92 dBm 底噪假设估算、带宽按频段与 PHY 推断，'
          'Windows 信号强度分辨率约 2 dB，这三项不是实测值。DXF / 手绘图纸属示意草图，不是测绘成果。</div>')

    # 多楼层 / 多会话合订汇总
    n = 8
    if sections:
        A('<h2>%d · 多楼层 / 多会话合订汇总</h2>' % n)
        A('<div class="note">下表按各会话固化的点位独立计算，使用同一套需求模板；'
          '合订只做汇总，不把不同楼层的点位混在一起做空间插值。</div>')
        A('<table><thead><tr><th>楼层 / 会话</th><th>点位</th><th>达标率</th><th>达标 / 边缘 / 不达标</th>'
          '<th>最弱 RSSI</th><th>平均 SINR</th><th>平均干扰指数</th></tr></thead><tbody>')
        for s in sections:
            A('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s / %s / %s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                _e(s.get("session")), _e(s.get("total")),
                _e("%s%%" % s["pass_rate"] if s.get("pass_rate") is not None else "-"),
                _e(s.get("pass", 0)), _e(s.get("edge", 0)), _e(s.get("fail", 0)),
                _e(s.get("rssi_min") if s.get("rssi_min") is not None else "-"),
                _e(s.get("sinr_avg") if s.get("sinr_avg") is not None else "-"),
                _e(s.get("index_avg") if s.get("index_avg") is not None else "-")))
        A('</tbody></table>')
        n += 1

    # 优化前后对比
    if diff.get("ok"):
        A('<h2>%d · 优化前后对比</h2>' % n)
        A('<div class="note">基线「%s」（%s） → 复测「%s」（%s）。%s</div>' % (
            _e((diff.get("before") or {}).get("name")), _e((diff.get("before") or {}).get("at_text")),
            _e((diff.get("after") or {}).get("name")), _e((diff.get("after") or {}).get("at_text")),
            _e(diff.get("note") or "")))
        A('<table><thead><tr><th>指标</th><th>优化前</th><th>优化后</th><th>变化</th><th>判读</th></tr></thead><tbody>')
        for m in diff.get("metrics") or []:
            if m.get("delta") is None:
                dcell, judge = "-", '<span class="delta-flat">数据不足</span>'
            else:
                cls = "delta-flat" if m.get("flat") else ("delta-up" if m.get("better") else "delta-down")
                dcell = '<span class="%s">%+g %s</span>' % (cls, m["delta"], _e(m.get("unit") or ""))
                judge = ('<span class="delta-flat">持平</span>' if m.get("flat")
                         else '<span class="delta-up">改善</span>' if m.get("better")
                         else '<span class="delta-down">退化</span>')
            A('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                _e(m.get("label")), _e(m.get("before")), _e(m.get("after")), dcell, judge))
        A('</tbody></table>')
        A('<h3>问题清单变化</h3>')
        A('<div class="kv"><div><b>新增问题</b>　%s</div><div><b>已消除</b>　%s</div>'
          '<div><b>严重度变化</b>　%s</div></div>' % (
              len(diff.get("added") or []), len(diff.get("removed") or []), len(diff.get("changed") or [])))
        for label, items in (("新增", diff.get("added") or []), ("已消除", diff.get("removed") or [])):
            for f in items[:12]:
                A('<div class="note" style="padding:7px 12px;margin:6px 0">%s · <b>%s</b>　%s</div>' % (
                    _e(label), _e(f.get("id")), _e(f.get("title"))))
        rows = diff.get("point_rows") or []
        if rows:
            A('<h3>逐点复测差异（按点位名称配对）</h3>')
            A('<table><thead><tr><th>点位</th><th>状态</th><th>优化前 RSSI</th><th>优化后 RSSI</th>'
              '<th>ΔRSSI</th><th>ΔSINR</th></tr></thead><tbody>')
            for r in rows[:60]:
                A('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                    _e(r.get("label")), _e(r.get("state")), _e(r.get("before_rssi")),
                    _e(r.get("after_rssi")),
                    ('%+g' % r["delta_rssi"]) if r.get("delta_rssi") is not None else "-",
                    ('%+g' % r["delta_sinr"]) if r.get("delta_sinr") is not None else "-"))
            A('</tbody></table>')

    if br.get("show_sign", True):
        A('<div class="sign"><div>勘测人 / 日期%s</div><div>复核人 / 日期%s</div><div>客户确认 / 日期</div></div>' % (
            ("　" + _e(br["engineer"])) if br.get("engineer") else "",
            ("　" + _e(br["reviewer"])) if br.get("reviewer") else ""))
    A('<footer><span>%s</span><span>%s · %s</span></footer>' % (
        _e(br.get("footer") or ("WirelessNetOptimizer · 单机离线版 v%s" % ver)), now, session))
    A('</div></body></html>')
    return "".join(parts)
