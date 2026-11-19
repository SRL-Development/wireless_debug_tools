# -*- coding: utf-8 -*-
"""
Wireless Net Optimizer - 无线故障排除 / 图纸热力图 / 无线优化
Windows 单机版：启动本地 Web 服务，自动打开浏览器。
零第三方依赖（仅 Python 标准库），可用 PyInstaller 打成单文件 exe。

数据说明：
  data/catalog.json          -> 993 条 AP/WLC/管理平面型号库
  data/plan.html             -> 方案与技术路线 v0.18
  data/wlc_offline_kb.json   -> WLC 离线知识包（官方码表 / 话术 / 处置步骤 / 文献索引，运行时零外网请求）
  wlan_scan.py / air_quality.py -> 空口扫描（Windows Native WiFi）与干扰评估
  roam_monitor.py            -> 移动漫游监测（当前关联 + 切换事件 + 信道拥挤）
  survey.py                  -> 现场勘测 / 需求验收 / 传播模型校准 / 智能审计 / 离线勘测报告
                                （对标 Ekahau Survey 与 NetAlly AirMagnet AirWISE 的本地实现）
  extras.py                  -> 报告模板 / 多楼层合订 / 基线 diff / CSV 导入 / 审计规则自定义
  desktop.py                 -> 本机只读诊断 / 扫描历史时间序列 / 离线 Word 报告 / 项目备份恢复
                                （不 ping、不联网、不发包、不改配置、不读明文密钥）
用户数据（图纸、项目、SSID、勘测点位）保存在 %APPDATA%/WirelessNetOptimizer/
"""
import os
import sys
import re
import json
import math
import time
import random
import base64
import socket
import hashlib
import subprocess
import threading
import webbrowser
import argparse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

VERSION = "0.14.0-beta"
APP_NAME = "WirelessNetOptimizer"
DEFAULT_PORT = 8765

# ---------------- 路径 ----------------
if getattr(sys, "frozen", False):
    BUNDLE = os.path.join(getattr(sys, "_MEIPASS", ""), "wireless_app")
else:
    BUNDLE = os.path.dirname(os.path.abspath(__file__))

WEB_DIR = os.path.join(BUNDLE, "web")
DATA_DIR = os.path.join(BUNDLE, "data")
USER_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
PROJECT_DIR = os.path.join(USER_DIR, "projects")


def ensure_dirs():
    for d in (USER_DIR, PROJECT_DIR):
        os.makedirs(d, exist_ok=True)


def log(*a):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(*a, flush=True)


# ---------------- 型号库 ----------------
CATALOG = {"stats": {}, "records": []}
try:
    with open(os.path.join(DATA_DIR, "catalog.json"), "r", encoding="utf-8") as f:
        CATALOG = json.load(f)
except Exception as e:
    log("[WARN] catalog load failed:", e)


# ---------------- 传播模型 ----------------
WALL_LOSS = {
    "drywall": 3.0, "glass": 3.0, "door": 3.0, "wood": 4.0,
    "brick": 7.0, "concrete": 12.0, "metal": 20.0,
}
BAND_PARAM = {
    "2.4": (46.0, 3.0),
    "5": (52.0, 3.2),
    "6": (54.0, 3.3),
}

# ---------------- SSID 配置（全局分析维度） ----------------
# 每个 SSID 决定：参与计算的 AP 集合、频段、最小接入 RSSI、VLAN 与安全套件。
# P1 接现网时，ap_ids / band / security 可由 WLC 的 WLAN Profile 直接映射。
DEFAULT_SSIDS = [
    dict(id="ssid-corp", name="Corp-Staff", purpose="员工办公", band="双频",
         security="WPA2/3-Enterprise · 802.1X", vlan=20, broadcast=True, enabled=True,
         min_rssi=-70, max_clients=80, ap_ids=[], note="全员主 SSID，所有 AP 广播；作为覆盖评估基线"),
    dict(id="ssid-guest", name="Guest-Portal", purpose="访客上网", band="双频",
         security="Portal / Captive Portal", vlan=90, broadcast=True, enabled=True,
         min_rssi=-72, max_clients=120, ap_ids=[], note="访客隔离，仅允许访问互联网；建议启用客户端隔离"),
    dict(id="ssid-voice", name="Voice-VoWiFi", purpose="语音 / IP 话机", band="5",
         security="WPA2-Enterprise · 802.1X", vlan=30, broadcast=True, enabled=True,
         min_rssi=-67, max_clients=40, ap_ids=[1, 2, 3, 4, 5], note="仅 5 GHz，需 802.11k/v/r + WMM/DSCP EF"),
    dict(id="ssid-iot", name="IoT-Sensor", purpose="物联网 / 扫码枪", band="2.4",
         security="WPA2-PSK / MAB", vlan=60, broadcast=False, enabled=True,
         min_rssi=-75, max_clients=60, ap_ids=[1, 3, 5, 7, 9], note="隐藏 SSID，仅 2.4 GHz，低速终端需限制速率集"),
    dict(id="ssid-agv", name="AGV-Prod", purpose="AGV / 产线漫游", band="5",
         security="WPA2-PSK", vlan=50, broadcast=True, enabled=True,
         min_rssi=-67, max_clients=30, ap_ids=[2, 4, 6, 8, 10], note="快速漫游场景，需保障重叠区与最小接入 RSSI"),
]
SSID_FILE = os.path.join(USER_DIR, "ssids.json")
SSID_STATE = {"active_id": "", "list": [], "auto_local": True}


def _ssid_defaults():
    return json.loads(json.dumps(DEFAULT_SSIDS))


def load_ssids():
    os.makedirs(USER_DIR, exist_ok=True)
    state = {"active_id": DEFAULT_SSIDS[0]["id"], "list": _ssid_defaults(), "auto_local": True}
    try:
        with open(SSID_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict) and saved.get("list"):
            state["list"] = saved["list"]
            state["active_id"] = saved.get("active_id") or state["list"][0]["id"]
            state["auto_local"] = bool(saved.get("auto_local", True))
    except Exception:
        pass
    if state["active_id"] not in [s.get("id") for s in state["list"]]:
        state["active_id"] = state["list"][0]["id"]
    SSID_STATE.update(state)
    return state


def save_ssids():
    with open(SSID_FILE, "w", encoding="utf-8") as f:
        json.dump(SSID_STATE, f, ensure_ascii=False, indent=1)


def ssid_get(sid=None, name=None):
    for s in SSID_STATE["list"]:
        if sid and str(s.get("id")) == str(sid):
            return s
        if name and s.get("name") == name:
            return s
    return None


def ssid_active():
    return ssid_get(SSID_STATE["active_id"]) or (SSID_STATE["list"][0] if SSID_STATE["list"] else None)


def ssid_filter_aps(aps, ssid):
    """按 SSID 的 AP 绑定过滤；ap_ids 为空表示所有 AP 广播该 SSID；停用 SSID 不产生覆盖。"""
    if not ssid:
        return list(aps or [])
    if ssid.get("enabled") is False:
        return []
    ids = ssid.get("ap_ids") or []
    if not ids:
        return list(aps or [])
    keep = {str(x) for x in ids}
    return [a for a in (aps or []) if str(a.get("id")) in keep]


def ssid_band(ssid, fallback="2.4"):
    b = (ssid or {}).get("band", "")
    return b if b in ("2.4", "5", "6") else fallback


def ssid_public():
    return {"ssids": SSID_STATE["list"], "active_id": SSID_STATE["active_id"],
            "active": ssid_active(), "auto_local": bool(SSID_STATE.get("auto_local", True))}


load_ssids()

# ---------------- 本机无线连接（Windows netsh，纯本地只读） ----------------
# 目的：测试机断网 / 只有 VPN 时也能把「这台电脑正在用的 SSID」变成分析对象。
# 只调用 netsh wlan show ...，不连接、不修改、不上传任何信息。
WLAN_KEYMAP = {
    "name": {"名称", "name"},
    "description": {"说明", "description"},
    "state": {"状态", "state"},
    "ssid": {"ssid"},
    "bssid": {"bssid", "apbssid"},
    "band_text": {"波段", "band", "频段"},
    "radio": {"无线电类型", "radiotype"},
    "auth": {"身份验证", "authentication"},
    "cipher": {"密码", "cipher", "加密"},
    "channel": {"信道", "channel", "通道"},
    "signal": {"信号", "signal"},
    "rssi": {"rssi", "信号强度"},
    "profile": {"配置文件", "profile"},
    "rx_rate": {"接收速率(mbps)", "接收速率", "receiverate(mbps)", "receiverate"},
    "tx_rate": {"传输速率(mbps)", "传输速率", "transmitrate(mbps)", "transmitrate"},
    "phy": {"物理地址", "physicaladdress"},
}
_WLAN_LOOKUP = {k: fld for fld, keys in WLAN_KEYMAP.items() for k in keys}


def _netsh(args):
    """只读调用 netsh；不弹控制台窗口，不修改任何无线配置。"""
    if not sys.platform.startswith("win"):
        return "", "本机无线读取仅支持 Windows"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        cp = subprocess.run(["netsh", "wlan"] + list(args), capture_output=True,
                            timeout=6, creationflags=flags)
        raw = (cp.stdout or b"") + (b"" if cp.returncode == 0 else (cp.stderr or b""))
        txt = ""
        for enc in ("utf-8", "gbk", "cp936", "mbcs"):
            try:
                txt = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if not txt:
            txt = raw.decode("utf-8", errors="replace")
        if cp.returncode != 0 and not txt.strip():
            return "", "netsh 返回码 %d" % cp.returncode
        return txt, ""
    except FileNotFoundError:
        return "", "未找到 netsh（系统组件缺失）"
    except subprocess.TimeoutExpired:
        return "", "读取本机无线状态超时"
    except Exception as ex:
        return "", str(ex)


def _wlan_blocks(text):
    """把 netsh 的接口输出按「名称」切成若干块，兼容中文 / 英文系统。"""
    blocks, cur = [], {}
    for line in (text or "").splitlines():
        sep = ":" if ":" in line else ("：" if "：" in line else "")
        if not sep:
            continue
        k, v = line.split(sep, 1)
        key = k.strip().lower().replace(" ", "").replace("\t", "")
        fld = _WLAN_LOOKUP.get(key)
        if not fld:
            continue
        if fld == "name" and cur:
            blocks.append(cur)
            cur = {}
        cur[fld] = v.strip()
    if cur:
        blocks.append(cur)
    return blocks


def _wlan_profiles(text):
    out = []
    keys = {"所有用户配置文件", "alluserprofile", "当前用户配置文件", "currentuserprofile"}
    for line in (text or "").splitlines():
        sep = ":" if ":" in line else ("：" if "：" in line else "")
        if not sep:
            continue
        k, v = line.split(sep, 1)
        if k.strip().lower().replace(" ", "") in keys:
            v = v.strip()
            if v and v not in out:
                out.append(v)
    return out


def _first_int(v):
    s = str(v or "").strip().split()
    if not s:
        return None
    try:
        return int(s[0].rstrip("%").split("(")[0])
    except ValueError:
        return None


def _band_from_channel(ch, radio, band_text=""):
    """优先用 netsh 的「波段」字段（最准），其次按信道，最后按无线电类型推断。"""
    mm = re.search(r"\d+(?:\.\d+)?", str(band_text or ""))
    if mm:
        v = float(mm.group())
        if v <= 3:
            return "2.4"
        if v <= 5.9:
            return "5"
        return "6"
    r = (radio or "").lower().replace(" ", "")
    if "6ghz" in r or "802.11be" in r:
        return "6"
    if ch is None:
        return "双频"
    if 1 <= ch <= 14:
        return "2.4"
    if 15 <= ch <= 233:
        return "5"
    return "双频"


def _iface_items(txt):
    items = []
    for b in _wlan_blocks(txt):
        st = b.get("state", "")
        chv = _first_int(b.get("channel"))
        items.append(dict(
            name=b.get("name", ""), description=b.get("description", ""), state=st,
            connected=("已连接" in st or "connected" in st.lower()),
            ssid=b.get("ssid", ""), bssid=b.get("bssid", ""),
            profile=b.get("profile", ""), radio=b.get("radio", ""),
            auth=b.get("auth", ""), cipher=b.get("cipher", ""),
            band_text=b.get("band_text", ""),
            channel=chv, signal_pct=_first_int(b.get("signal")),
            rssi_dbm=_first_int(b.get("rssi")),
            rx_rate=b.get("rx_rate", ""), tx_rate=b.get("tx_rate", ""),
            band=_band_from_channel(chv, b.get("radio", ""), b.get("band_text", ""))))
    return items


def wlan_iface_state():
    """轻量版：只调 netsh show interfaces（约 100-200 ms），用于漫游监测的高频轮询。"""
    txt, err = _netsh(["show", "interfaces"])
    items = _iface_items(txt)
    conn = next((x for x in items if x["connected"] and x["ssid"]), None)
    out = {"available": bool(conn), "connected": conn, "interfaces": items,
           "error": err or "", "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not items and not out["error"]:
        out["error"] = ("WLAN AutoConfig 服务未运行，或本机没有无线网卡"
                        if ("not running" in (txt or "").lower() or "未运行" in (txt or ""))
                        else "未检测到无线接口（可能正在使用有线网络）")
    return out


def wlan_local_scan():
    info = {"platform": sys.platform, "source": "netsh wlan show interfaces",
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "available": False, "interfaces": [], "connected": None,
            "profiles": [], "error": ""}
    txt, err = _netsh(["show", "interfaces"])
    if err:
        info["error"] = err
    info["interfaces"] = _iface_items(txt)
    for it in info["interfaces"]:
        if it["connected"] and it["ssid"]:
            info["connected"] = it
            break
    info["available"] = bool(info["connected"])
    ptxt, perr = _netsh(["show", "profiles"])
    info["profiles"] = _wlan_profiles(ptxt)
    if perr and not info["error"]:
        info["error"] = perr
    if not info["interfaces"] and not info["error"]:
        info["error"] = ("WLAN AutoConfig 服务未运行，或本机没有无线网卡"
                         if ("not running" in (txt or "").lower() or "未运行" in (txt or ""))
                         else "未检测到无线接口（可能正在使用有线网络）")
    return info


def wlan_use_ssid(req):
    """把本机当前连接（或指定的本机已保存配置）写入 SSID 列表并设为分析对象。"""
    scan = wlan_local_scan()
    name = str(req.get("name") or (scan.get("connected") or {}).get("ssid") or "").strip()
    if not name:
        return {"error": "本机当前未连接 Wi-Fi，请从「本机已保存配置」里选一个 SSID"}
    conn = scan.get("connected") or {}
    src = conn if conn.get("ssid") == name else next(
        (x for x in scan["interfaces"] if x.get("ssid") == name), {})
    band = str(req.get("band") or src.get("band") or "双频")
    if band not in ("双频", "2.4", "5", "6"):
        band = "双频"
    local_meta = dict(interface=src.get("name", ""), bssid=src.get("bssid", ""),
                      channel=src.get("channel"), band_inferred=src.get("band", ""),
                      band_text=src.get("band_text", ""),
                      signal_pct=src.get("signal_pct"), rssi_dbm=src.get("rssi_dbm"),
                      radio=src.get("radio", ""), auth=src.get("auth", ""),
                      connected=bool(src.get("connected")),
                      checked_at=scan["checked_at"])
    note = ("由本机 netsh 只读生成：%s / %s / ch%s / 信号 %s%% (RSSI %s dBm) / %s；"
            "频段按本机波段与信道判定，请现场核对。"
            % (src.get("name") or "-", src.get("band_text") or src.get("band") or "-",
               src.get("channel") if src.get("channel") is not None else "-",
               src.get("signal_pct") if src.get("signal_pct") is not None else "-",
               src.get("rssi_dbm") if src.get("rssi_dbm") is not None else "-",
               scan["checked_at"]))
    existing = ssid_get(name=name)
    created = existing is None
    if created:
        sid = "ssid-local-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
        SSID_STATE["list"].append(dict(
            id=sid, name=name, purpose="本机当前 Wi-Fi", band=band,
            security=src.get("auth") or "本机读取（未标注）",
            vlan=int(req.get("vlan") or 0), broadcast=True, enabled=True,
            min_rssi=float(req.get("min_rssi") or -70), max_clients=60,
            ap_ids=[], source="local", local=local_meta, note=note))
    else:
        sid = existing["id"]
        existing.update(source="local", local=local_meta, note=note)
        if not existing.get("band_locked"):
            existing["band"] = band
    SSID_STATE["active_id"] = sid
    save_ssids()
    return {"state": ssid_public(), "local": scan, "created": created,
            "ssid": ssid_get(sid)}

# 非 Wi-Fi / 邻区干扰源库：kind, 频段, 中心频率 MHz, 带宽 MHz, 1m 处等效 dBm, 占空比, 处置建议
INTERFERER_LIBRARY = [
    ("微波炉", "2.4", 2437, 22, -46, .55, "远离 AP 与主干覆盖区，更换屏蔽更好的设备；干扰只在加热时出现，可按时间轴关联投诉"),
    ("无线摄像头", "2.4", 2462, 20, -54, .80, "改有线或 5 GHz 图传；避免在 ch11 附近布放 AP"),
    ("婴儿监视器 / 模拟图传", "2.4", 2432, 12, -57, .65, "更换为数字跳频或 5 GHz 型号，避开 2.4G 中段"),
    ("蓝牙耳机 / 音箱", "2.4", 2440, 2, -64, .25, "跳频低占空比，通常无需处置；高密区域可引导终端到 5 GHz"),
    ("Zigbee / 物联网网关", "2.4", 2425, 3, -66, .18, "核对 Zigbee 信道与 Wi-Fi 1/6/11 的错位规划，避免网关紧贴 AP"),
    ("无线投屏 / WiDi", "5", 5745, 40, -59, .45, "限制投屏带宽或改用有线；确认未长期占用 5G 高频信道"),
    ("雷达 / DFS 源", "5", 5260, 20, -62, .30, "改用非 DFS 信道（36-48 / 149-165），避免强制信道切换"),
    ("邻楼 / 邻区 AP", "5", 5180, 40, -68, .70, "协调信道与功率边界，降低发射功率或调整天线方向"),
    ("无线键鼠 / 外设", "2.4", 2475, 2, -71, .12, "低功率突发，一般仅影响紧贴的终端"),
]
CH24 = list(range(1, 15))
NON_OVERLAP_24 = [1, 6, 11]
CH5_GROUPS = {"36-64": [36, 40, 44, 48, 52, 56, 60, 64],
              "100-140": [100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140],
              "149-165": [149, 153, 157, 161, 165]}


def prep_interferers(items, band, ppm):
    """把干扰源转成 (x, y, power, duty, radius_px)，仅保留与计算频段相关的源。"""
    out = []
    for it in items or []:
        b = str(it.get("band", "2.4"))
        if b != band and not (band in ("5", "6") and b in ("5", "6")):
            continue
        try:
            x, y = float(it["x"]), float(it["y"])
            power = float(it.get("power", -55))
            duty = max(0.0, min(1.0, float(it.get("duty", .5))))
        except Exception:
            continue
        radius_px = max(2.0, float(it.get("radius_m", 18)) * ppm)
        out.append((x, y, power, duty, radius_px))
    return out


def noise_at(px, py, base_noise, interferers, ppm):
    """线性域功率叠加，返回该点受干扰抬升后的等效噪声底 dBm。"""
    p = 10.0 ** (base_noise / 10.0)
    for (x, y, power, duty, radius_px) in interferers:
        d_px = math.hypot(px - x, py - y)
        if d_px > radius_px:
            continue
        d_m = max(d_px / max(ppm, .01), 1.0)
        rx = power - 20.0 * math.log10(d_m)
        p += 10.0 ** (rx / 10.0) * duty
    return 10.0 * math.log10(p)


def prep_walls(walls):
    out = []
    for w in walls or []:
        try:
            x1, y1, x2, y2 = (float(w[k]) for k in ("x1", "y1", "x2", "y2"))
        except Exception:
            continue
        loss = WALL_LOSS.get(str(w.get("material", "drywall")), 3.0)
        out.append((x1, y1, x2, y2, x2 - x1, y2 - y1, loss,
                    min(x1, x2) - 0.5, max(x1, x2) + 0.5,
                    min(y1, y2) - 0.5, max(y1, y2) + 0.5))
    return out


def wall_loss_for(px, py, ax, ay, walls):
    """射线 (px,py)->(ax,ay) 穿过的墙体总衰减 dB。"""
    rx, ry = ax - px, ay - py
    total = 0.0
    for (x1, y1, x2, y2, sx, sy, loss, bx1, bx2, by1, by2) in walls:
        # 包围盒快速排除
        lo_x, hi_x = (px, ax) if px < ax else (ax, px)
        lo_y, hi_y = (py, ay) if py < ay else (ay, py)
        if hi_x < bx1 or lo_x > bx2 or hi_y < by1 or lo_y > by2:
            continue
        qx, qy = x1 - px, y1 - py
        denom = rx * sy - ry * sx
        if abs(denom) < 1e-9:
            continue
        t = (qx * sy - qy * sx) / denom
        u = (qx * ry - qy * rx) / denom
        if 0.0 < t < 1.0 and 0.0 <= u <= 1.0:
            total += loss
    return min(total, 42.0)


def prep_aps(aps, band):
    out = []
    for a in aps or []:
        if a.get("enabled") is False:
            continue
        if band in ("5", "6") and str(a.get("band", "2.4")) == "2.4":
            continue
        try:
            x, y = float(a["x"]), float(a["y"])
        except Exception:
            continue
        tx = float(a.get("tx_power", 20))
        gain = float(a.get("gain", 3))
        out.append((x, y, tx, gain))
    return out


def predict_at(px, py, ap, walls, pl0, n, ppm):
    """传播先验。AP 近场（< 3 m）的墙体损耗按比例折减：
    AP 贴墙 / 挂墙安装时，射线在极短距离内可能穿过多段墙，累加出 30~42 dB 的
    虚假黑洞；实际近场由直射与绕射主导。3 m 以外恢复完整墙损。"""
    d_px = math.hypot(px - ap[0], py - ap[1])
    d_m = max(d_px / max(ppm, 0.01), 1.0)
    pl = pl0 + 10.0 * n * math.log10(d_m)
    wl = wall_loss_for(px, py, ap[0], ap[1], walls)
    if wl:
        near_scale = 0.35 + 0.65 * min(1.0, d_px / (3.0 * max(ppm, 0.01)))
        pl += wl * near_scale
    return ap[2] + ap[3] - pl


def heatmap_engine(req):
    W = max(80.0, float(req.get("width", 1000)))
    H = max(60.0, float(req.get("height", 700)))
    cols = max(20, min(200, int(req.get("cols", 120))))
    rows = max(15, min(160, int(req.get("rows", 84))))
    ppm = max(0.5, float(req.get("ppm", 8.0)))
    band = str(req.get("band", "2.4"))
    # SSID 是全局分析维度：决定参与计算的 AP 集合、生效频段与走测打点归属
    ssid = ssid_get(req.get("ssid_id")) or ssid_get(name=req.get("ssid"))
    if ssid:
        band = ssid_band(ssid, band)
        ssid_name = ssid.get("name", "")
    else:
        ssid_name = "全部 SSID"
    pl0_def, n_def = BAND_PARAM.get(band, BAND_PARAM["2.4"])
    # 「现场勘测」页用实测点校准后，可把拟合出的 PL0 / 路径损耗指数 n 写回预测模型
    try:
        pl0 = float(req["pl0"]) if req.get("pl0") not in (None, "") else pl0_def
        n = float(req["path_n"]) if req.get("path_n") not in (None, "") else n_def
    except (TypeError, ValueError):
        pl0, n = pl0_def, n_def
    n = max(1.6, min(6.0, n))
    noise = float(req.get("noise", -95))
    walls = prep_walls(req.get("walls"))
    aps = prep_aps(ssid_filter_aps(req.get("aps"), ssid), band)
    interferers = prep_interferers(req.get("interferers"), band, ppm)
    cov_thr = float((ssid or {}).get("min_rssi", -67))
    samples = []
    for s in req.get("samples") or []:
        # 打点带 ssid 时只用于该 SSID；无 ssid 的视为通用（兼容旧项目 / 示例数据）
        tag = s.get("ssid")
        if tag and ssid and tag != ssid_name:
            continue
        try:
            samples.append((float(s["x"]), float(s["y"]), float(s["rssi"])))
        except Exception:
            continue

    # 实测残差（模型预测 vs 实测）
    use_res = bool(req.get("use_residuals", True))
    residuals = []
    for (sx, sy, rssi) in samples:
        pred = max(predict_at(sx, sy, a, walls, pl0, n, ppm) for a in aps) if aps else -95.0
        residuals.append((sx, sy, rssi - pred))

    # 稳健化：残差是「当前 AP 布局」下的校准量，不是固定的空间偏移场。
    # AP 移动后，旧打点会与新布局严重失配（把 AP 拖到打点上，残差可达 -30 dB）。
    # 若把这种残差直接 IDW 插值成固定偏移，会在 AP 脚下挖出低信号洞，
    # 表现为「离 AP 越近反而越像盲区，远处反而正常」。
    # 处理：中位数代表系统性偏差，Huber 软限幅 ±10 dB，失配打点权重降到 0.15。
    res_field, res_median, outliers = [], 0.0, 0
    if residuals:
        vals = sorted(r[2] for r in residuals)
        k = len(vals) // 2
        res_median = vals[k] if len(vals) % 2 else (vals[k - 1] + vals[k]) / 2.0
        for (sx, sy, res) in residuals:
            dev = res - res_median
            bad = abs(dev) > 12.0
            outliers += 1 if bad else 0
            res_field.append((sx, sy, res_median + max(-10.0, min(10.0, dev)),
                              0.15 if bad else 1.0))
    if not use_res:
        res_field = []

    # 留一交叉验证 RMSE（与渲染使用同一套残差权重，保证指标与画面一致）
    rmse = None
    pool = res_field if res_field else [(x, y, 0.0, 1.0) for (x, y, _) in residuals]
    if len(pool) >= 3 and aps:
        errs = []
        for i, (sx, sy, meas) in enumerate(samples):
            pred = max(predict_at(sx, sy, a, walls, pl0, n, ppm) for a in aps) if aps else -95.0
            num = den = 0.0
            for j, (rx, ry, res, rel) in enumerate(pool):
                if i == j:
                    continue
                d2 = (sx - rx) ** 2 + (sy - ry) ** 2 + 400.0
                w = rel / (d2 * d2)
                num += w * res
                den += w
            errs.append((pred + (num / den if den else 0.0) - meas) ** 2)
        rmse = round(math.sqrt(sum(errs) / len(errs)), 2)

    rssi_grid, conf_grid, overlap_grid, best_grid, sinr_grid = [], [], [], [], []
    cell_w, cell_h = W / cols, H / rows
    count_cov = count_dead = count_lowconf = count_lowsinr = 0
    sum_rssi = sum_sinr = sum_noise_rise = 0.0

    for r in range(rows):
        py = (r + 0.5) * cell_h
        for c in range(cols):
            px = (c + 0.5) * cell_w
            best, second, best_idx = -110.0, -110.0, -1
            for idx, a in enumerate(aps):
                v = predict_at(px, py, a, walls, pl0, n, ppm)
                if v > best:
                    second, best, best_idx = best, v, idx
                elif v > second:
                    second = v
            conf, overlap = 0.0, 0.0
            if aps:
                raw_best = best
                # 重叠度用传播先验原始值，避免被残差校正污染
                overlap = max(0.0, min(1.0, ((second - raw_best) + 20.0) / 20.0)) if second > -100 else 0.0
                num = den = 0.0
                dmin2 = float("inf")
                for (rx, ry, res, rel) in res_field:
                    d2 = (px - rx) ** 2 + (py - ry) ** 2 + 400.0
                    if d2 < dmin2:
                        dmin2 = d2
                    w = rel / (d2 * d2)
                    num += w * res
                    den += w
                corr = max(-8.0, min(8.0, num / den if den else 0.0))
                # 近场保护：AP 3 m 内校正渐入，AP 脚下完全由传播模型决定。
                # 近场本来就是自由空间直射主导，实测校准只应用于中远场阴影衰落。
                d_ap = min(math.hypot(px - a[0], py - a[1]) for a in aps)
                w_near = min(1.0, (d_ap / (3.0 * ppm)) ** 2)
                best = raw_best + corr * w_near
                conf = 0.40 + 0.60 * math.exp(-math.sqrt(dmin2) / (9.0 * ppm)) if residuals else 0.40
            val = round(best, 1)
            eff_noise = noise_at(px, py, noise, interferers, ppm) if interferers else noise
            sinr = best - eff_noise
            rssi_grid.append(val)
            conf_grid.append(round(conf, 2))
            overlap_grid.append(round(overlap, 2))
            best_grid.append(best_idx)
            sinr_grid.append(round(sinr, 1))
            sum_rssi += best
            sum_sinr += sinr
            sum_noise_rise += eff_noise - noise
            if best >= cov_thr:
                count_cov += 1
            if best < -80:
                count_dead += 1
            if conf < 0.55:
                count_lowconf += 1
            if sinr < 20:
                count_lowsinr += 1

    total = cols * rows
    stats = {
        "grid": "%d x %d" % (cols, rows),
        "ap_count": len(aps), "wall_count": len(walls), "sample_count": len(samples),
        "coverage_pct": round(100.0 * count_cov / total, 1),
        "deadzone_pct": round(100.0 * count_dead / total, 1),
        "low_conf_pct": round(100.0 * count_lowconf / total, 1),
        "avg_rssi": round(sum_rssi / total, 1) if aps else None,
        "avg_sinr": round(sum_sinr / total, 1) if aps else None,
        "low_sinr_pct": round(100.0 * count_lowsinr / total, 1) if aps else None,
        "avg_noise_rise_db": round(sum_noise_rise / total, 1),
        "interferer_count": len(interferers),
        "holdout_rmse_db": rmse,
        "ssid": ssid_name, "ssid_band": (ssid or {}).get("band", "双频"),
        "ssid_ap_total": len(ssid_filter_aps(req.get("aps"), ssid)),
        "coverage_threshold": cov_thr,
        "residuals_enabled": use_res,
        "residual_outliers": outliers,
        "residual_median_db": round(res_median, 1),
        "nearfield_guard_m": 3.0,
        "band": band, "noise_dbm": noise, "ppm": ppm,
        "path_loss_n": round(n, 2), "pl0_db": round(pl0, 1),
        "model": ("ITU-R P.1238 简化 + 多墙衰减" +
                  ("（实测校准 n=%.2f / PL0=%.1f）" % (n, pl0)
                   if abs(n - n_def) > 1e-6 or abs(pl0 - pl0_def) > 1e-6 else "") +
                  (" + 实测残差稳健校正（近场保护 3 m）" if use_res else "（残差校正已关闭）") +
                  (" + 干扰噪声抬升" if interferers else "")),
    }
    return {
        "cols": cols, "rows": rows,
        "rssi": rssi_grid, "conf": conf_grid,
        "overlap": overlap_grid, "best": best_grid, "sinr": sinr_grid,
        "stats": stats,
    }


# ---------------- 无线干扰 / 现场连接状态引擎 ----------------
# 当前为模拟数据源；P1 接现网时只需替换采集层，客户端字段已按 SNMP v2c / WLC API 建模。
LIVE = {"aps": [], "clients": [], "interferers": [], "events": [],
        "ppm": 10.0, "tick": 0.0, "step": 0, "seq": 0, "paused": False}

CLIENT_NAMES = ["笔记本", "手机", "PDA 扫码枪", "IP 话机", "打印机", "会议终端", "AGV 车载", "摄像头", "平板", "工位 PC"]
CLIENT_OUI = ["3C:22:FB", "A4:83:E7", "00:1A:2B", "B8:27:EB", "F0:9F:C2", "5C:CF:7F"]
DEFAULT_APS = [
    (180, 350, "CW9176I", 1), (400, 350, "5760-10", 6), (640, 350, "RG-AP9220", 11), (860, 350, "FAP-431G", 1),
    (140, 160, "WA7539", 6), (560, 160, "MR57", 11), (860, 160, "AP-755", 1),
    (170, 520, "U7 Pro", 6), (620, 520, "AP47", 11), (880, 520, "R770", 1),
]


def _event(level, typ, text, client="", ap=""):
    LIVE["seq"] += 1
    LIVE["events"].insert(0, dict(id=LIVE["seq"], ts=time.strftime("%H:%M:%S"), level=level,
                                  type=typ, text=text, client=client, ap=ap))
    del LIVE["events"][80:]


def _mac():
    return "%s:%02X:%02X:%02X" % (random.choice(CLIENT_OUI), random.randint(0, 255),
                                   random.randint(0, 255), random.randint(0, 255))


def _ssid_for_client(name, idx):
    """按终端类型把客户端归到某个 SSID；P1 接现网时直接读 WLC 客户端表的 SSID 字段。"""
    pool = [s for s in SSID_STATE["list"] if s.get("enabled") is not False] or SSID_STATE["list"]
    if not pool:
        return "Corp-Staff", "ssid-corp", 20
    pick = lambda nm: next((s for s in pool if s.get("name") == nm), None)
    if "AGV" in name:
        s = pick("AGV-Prod")
    elif "IP 话机" in name or "会议终端" in name:
        s = pick("Voice-VoWiFi")
    elif any(k in name for k in ("PDA", "摄像头", "打印机")):
        s = pick("IoT-Sensor")
    elif idx % 11 == 0:
        s = pick("Guest-Portal")
    else:
        # 普通终端按「通用 SSID」轮流分配：本机/扫描来源优先排前面，
        # 这样任何新建的 SSID 都能分到终端，按 SSID 筛选不会是空表。
        special = {"AGV-Prod", "Voice-VoWiFi", "IoT-Sensor", "Guest-Portal"}
        general = [x for x in pool if x.get("name") not in special]
        if not general:
            general = pool
        general.sort(key=lambda x: (0 if x.get("source") in ("local", "scan") else 1,
                                    str(x.get("name") or "")))
        s = general[(idx - 1) % len(general)]
    s = s or pool[0]
    return s.get("name", "-"), s.get("id", ""), s.get("vlan", 0)


def _signal_at(x, y, aps, ppm):
    best, best_ap = -100.0, None
    for a in aps:
        d = max(math.hypot(x - a["x"], y - a["y"]) / max(ppm, .01), 1.0)
        v = (a.get("tx_power", 20) + a.get("gain", 4)) - (46 + 30 * math.log10(d)) - 4
        if v > best:
            best, best_ap = v, a
    return best, best_ap


def _rate_of(rssi, snr):
    if snr >= 32: return 9, 433
    if snr >= 27: return 8, 292
    if snr >= 23: return 7, 195
    if snr >= 19: return 6, 130
    if snr >= 15: return 5, 78
    if snr >= 11: return 4, 39
    if snr >= 7: return 3, 26
    return 1, 6.5


def _client_status(c):
    if c["auth"] != "已认证": return "认证异常"
    if c["rssi"] < -75: return "弱信号"
    if c["snr"] < 18: return "受干扰"
    if c["retry"] > 25: return "高重传"
    if c["roam_count"] >= 3: return "频繁漫游"
    if c["rate"] <= 26: return "低速率"
    return "正常"


def live_configure(aps=None, ppm=10, n_clients=36):
    src = [dict(a) for a in (aps or [])]
    if not src:
        src = [dict(id=i + 1, x=x, y=y, model=m, channel=ch, tx_power=20, gain=4, band="5")
               for i, (x, y, m, ch) in enumerate(DEFAULT_APS)]
    for i, a in enumerate(src):
        a.setdefault("id", i + 1)
        a["x"], a["y"] = float(a.get("x", 500)), float(a.get("y", 350))
        a["tx_power"] = float(a.get("tx_power", 20))
        a["gain"] = float(a.get("gain", 4))
        if not a.get("channel"):
            a["channel"] = NON_OVERLAP_24[i % 3] if str(a.get("band", "5")) == "2.4" else [36, 44, 149, 157][i % 4]
        a["channel"] = int(a["channel"])
    n = max(4, min(120, int(n_clients or 36)))
    ppm = max(.5, float(ppm or 10))
    LIVE.update(aps=src, ppm=ppm, clients=[], interferers=[], events=[],
                tick=time.time(), step=0, seq=0, paused=False)
    for i, (kind, b, center, width, power, duty, advice) in enumerate(random.sample(INTERFERER_LIBRARY, 6)):
        LIVE["interferers"].append(dict(
            id=i + 1, kind=kind, band=b, center=center, width=width,
            power=round(power + random.uniform(-3, 3), 1),
            duty=round(max(.05, min(.95, duty + random.uniform(-.1, .1))), 2),
            x=round(random.uniform(90, 910)), y=round(random.uniform(80, 620)),
            radius_m=18, advice=advice))
    ifs = prep_interferers(LIVE["interferers"], "2.4", ppm)
    for i in range(n):
        x, y = random.uniform(60, 940), random.uniform(60, 640)
        rssi, ap = _signal_at(x, y, src, ppm)
        rssi = max(-92, min(-32, rssi + random.uniform(-4, 4)))
        noise = noise_at(x, y, -95, ifs, ppm)
        snr = rssi - noise
        mcs, rate = _rate_of(rssi, snr)
        band = random.choice(["2.4", "2.4", "5"])
        sname, sid, svlan = _ssid_for_client("%s" % CLIENT_NAMES[i % len(CLIENT_NAMES)], i + 1)
        cname = "%s-%02d" % (CLIENT_NAMES[i % len(CLIENT_NAMES)], i + 1)
        c = dict(
            id=i + 1, name=cname, mac=_mac(), ssid=sname, ssid_id=sid, vlan=svlan,
            ap_id=ap["id"] if ap else 0, ap_name="AP-%s" % (ap["id"] if ap else 0),
            band=band, channel=ap["channel"] if ap else 1,
            rssi=round(rssi, 1), snr=round(snr, 1), mcs=mcs, rate=rate,
            retry=round(max(1, 6 + max(0, 25 - snr) * 1.8 + random.uniform(-2, 2)), 1),
            rx_mbps=round(random.uniform(.2, max(1, rate / 8)), 1),
            tx_mbps=round(random.uniform(.1, max(.5, rate / 14)), 1),
            auth=random.choice(["已认证"] * 24 + ["待认证"] * 2),
            ip="10.20.%d.%d" % (random.randint(1, 12), random.randint(2, 250)),
            uptime_s=random.randint(40, 9000), x=round(x), y=round(y),
            roam_count=0, status="正常", last_event="-")
        c["status"] = _client_status(c)
        c["anomaly"] = ""
        LIVE["clients"].append(c)
    # 注入典型现场问题，保证「现场状态」页有可排查对象；anomaly 让异常在实时刷新中保持
    pool = random.sample(LIVE["clients"], min(14, len(LIVE["clients"])))
    for i, c in enumerate(pool):
        kind = i % 6
        if kind == 0:      # 弱信号：放到楼层边角
            c["x"], c["y"] = random.choice([(70, 70), (930, 70), (70, 630), (930, 630)])
            c["rssi"] = round(random.uniform(-85, -77), 1)
            c["anomaly"] = "weak"
        elif kind == 1:    # 受干扰：放到干扰源附近
            it = LIVE["interferers"][i % len(LIVE["interferers"])]
            c["x"] = round(max(60, min(940, it["x"] + random.uniform(-55, 55))))
            c["y"] = round(max(60, min(640, it["y"] + random.uniform(-55, 55))))
            c["rssi"] = round(random.uniform(-63, -52), 1)
            c["snr"] = round(random.uniform(9, 16), 1)
            c["anomaly"] = "interfered"
        elif kind == 2:
            c["retry"] = round(random.uniform(28, 45), 1)
            c["anomaly"] = "retry"
        elif kind == 3:
            c["rate"], c["mcs"] = 26, 3
            c["anomaly"] = "lowrate"
        elif kind == 4:
            c["roam_count"] = random.randint(3, 7)
            c["last_event"] = "多次跨 AP 漫游"
            c["anomaly"] = "roam"
        else:
            c["auth"] = "认证失败"
            c["last_event"] = "802.1X 认证失败"
            c["anomaly"] = "auth"
        c["status"] = _client_status(c)
    _event("info", "system", "现场采集已启动：%d 台 AP / %d 个终端 / %d 个干扰源" % (len(src), n, len(LIVE["interferers"])))
    _event("warn", "interference", "2.4 GHz 检测到非 Wi-Fi 干扰，ch6 底噪抬升")
    _event("error", "auth", "2 台终端 802.1X 认证失败，请核查 RADIUS")
    _event("warn", "disconnect", "会议室区域 1 台终端异常掉线后重连")
    _event("info", "roam", "AGV 车载终端在 AP-3 / AP-4 之间频繁漫游")
    return dict(ok=True, aps=len(src), clients=n, interferers=len(LIVE["interferers"]))


def _advance():
    now = time.time()
    if LIVE["paused"]:
        LIVE["tick"] = now
        return
    ticks = min(3, int((now - LIVE["tick"]) / 2.0))
    if ticks <= 0:
        return
    LIVE["tick"] = now
    aps, ppm = LIVE["aps"], LIVE["ppm"]
    for _ in range(ticks):
        LIVE["step"] += 1
        for it in LIVE["interferers"]:
            it["duty"] = round(max(.03, min(.98, it["duty"] + random.uniform(-.05, .05))), 2)
        ifs = prep_interferers(LIVE["interferers"], "2.4", ppm)
        for c in LIVE["clients"]:
            c["rssi"] = round(max(-92, min(-32, c["rssi"] + random.uniform(-1.4, 1.4))), 1)
            noise = noise_at(c["x"], c["y"], -95, ifs, ppm)
            c["snr"] = round(c["rssi"] - noise, 1)
            c["mcs"], c["rate"] = _rate_of(c["rssi"], c["snr"])
            c["retry"] = round(max(1, min(60, 6 + max(0, 25 - c["snr"]) * 1.8 + random.uniform(-2, 2))), 1)
            c["rx_mbps"] = round(random.uniform(.2, max(1, c["rate"] / 8)), 1)
            c["tx_mbps"] = round(random.uniform(.1, max(.5, c["rate"] / 14)), 1)
            c["uptime_s"] += 2
            r = random.random()
            if r < .010:
                _, ap = _signal_at(c["x"], c["y"], aps, ppm)
                if ap and ap["id"] != c["ap_id"]:
                    old = c["ap_name"]
                    c["ap_id"], c["ap_name"], c["channel"] = ap["id"], "AP-%s" % ap["id"], ap["channel"]
                    c["roam_count"] += 1
                    c["last_event"] = "漫游 %s → %s" % (old, c["ap_name"])
                    _event("info", "roam", "%s 从 %s 漫游到 %s" % (c["name"], old, c["ap_name"]), c["name"], c["ap_name"])
            elif r < .015:
                c["auth"] = "认证失败"
                c["last_event"] = "802.1X 认证失败"
                _event("error", "auth", "%s 认证失败，请核查 RADIUS 与证书" % c["name"], c["name"], c["ap_name"])
            elif r < .019:
                c["uptime_s"] = 0
                c["last_event"] = "掉线重连"
                _event("warn", "disconnect", "%s 异常掉线后重新关联" % c["name"], c["name"], c["ap_name"])
            elif r < .021 and c["auth"] != "已认证":
                c["auth"] = "已认证"
                c["last_event"] = "认证恢复"
                _event("info", "auth", "%s 认证恢复" % c["name"], c["name"], c["ap_name"])
            a = c.get("anomaly", "")
            if a == "weak": c["rssi"] = round(min(c["rssi"], -76 + random.uniform(-2, 2)), 1)
            elif a == "interfered": c["snr"] = round(min(c["snr"], 15 + random.uniform(-2, 2)), 1)
            elif a == "retry": c["retry"] = round(max(c["retry"], 28 + random.uniform(-3, 3)), 1)
            elif a == "lowrate": c["mcs"], c["rate"] = 3, 26
            elif a == "auth": c["auth"] = "认证失败"
            c["status"] = _client_status(c)


def live_snapshot():
    if not LIVE["clients"]:
        live_configure()
    _advance()
    cs = LIVE["clients"]
    online = [c for c in cs if c["auth"] == "已认证"]
    healthy = [c for c in online if c["status"] == "正常"]
    def avg(key, seq):
        return round(sum(float(x[key]) for x in seq) / len(seq), 1) if seq else None
    ev = LIVE["events"]
    kpi = dict(
        total=len(cs), online=len(online), healthy_pct=round(100.0 * len(healthy) / len(online), 1) if online else 0,
        avg_rssi=avg("rssi", online), avg_snr=avg("snr", online), avg_retry=avg("retry", online),
        weak=sum(1 for c in online if c["status"] == "弱信号"),
        interfered=sum(1 for c in online if c["status"] == "受干扰"),
        low_rate=sum(1 for c in online if c["status"] == "低速率"),
        auth_fail=sum(1 for c in cs if c["auth"] != "已认证"),
        roam=sum(1 for e in ev if e["type"] == "roam"),
        disconnect=sum(1 for e in ev if e["type"] == "disconnect"),
        events=len(ev), interferers=len(LIVE["interferers"]),
    )
    ssid_stats = []
    for s in SSID_STATE["list"]:
        grp = [c for c in cs if c.get("ssid") == s.get("name")]
        ok = [c for c in grp if c.get("auth") == "已认证" and c.get("status") == "正常"]
        ssid_stats.append(dict(id=s.get("id"), name=s.get("name"), band=s.get("band"),
                               purpose=s.get("purpose"), vlan=s.get("vlan"),
                               enabled=s.get("enabled", True), broadcast=s.get("broadcast", True),
                               security=s.get("security"), min_rssi=s.get("min_rssi"),
                               max_clients=s.get("max_clients"), ap_ids=s.get("ap_ids", []),
                               note=s.get("note", ""), clients=len(grp),
                               healthy_pct=round(100.0 * len(ok) / len(grp), 1) if grp else 0,
                               avg_rssi=avg("rssi", grp), avg_snr=avg("snr", grp)))
    return dict(ts=time.strftime("%H:%M:%S"), step=LIVE["step"], paused=LIVE["paused"],
                ppm=LIVE["ppm"], kpi=kpi, clients=cs, events=ev[:40],
                interferers=LIVE["interferers"], aps=LIVE["aps"],
                ssid_stats=ssid_stats, active_ssid=ssid_active())


def interference_analysis(aps=None, ppm=10, band="2.4", ssid_id=None):
    ssid = ssid_get(ssid_id) or ssid_active()
    if ssid:
        band = ssid_band(ssid, band)
    base = [dict(a) for a in (aps if aps else (LIVE["aps"] or []))]
    if not base:
        base = [dict(id=i + 1, x=x, y=y, model=m, channel=ch, tx_power=20, gain=4, band="5")
                for i, (x, y, m, ch) in enumerate(DEFAULT_APS)]
    src = [dict(a) for a in ssid_filter_aps(base, ssid)]
    for i, a in enumerate(src):
        a.setdefault("id", i + 1)
        a.setdefault("channel", NON_OVERLAP_24[i % 3])
        a["channel"] = int(a["channel"])
        a.setdefault("x", 500); a.setdefault("y", 350)
    ppm = max(.5, float(ppm or 10))
    ifs = LIVE["interferers"]
    spectrum = []
    for ch in CH24:
        f = 2412 + 5 * (ch - 1)
        on_ap = [a for a in src if abs(a["channel"] - ch) <= 1 and a["channel"] <= 14]
        p = 10.0 ** (-100.0 / 10.0)
        for it in ifs:
            if it["band"] != "2.4":
                continue
            span = (it["width"] + 22) / 2.0
            sep = abs(f - it["center"])
            if sep < span:
                coupling = 1.0 - sep / span
                lvl = it["power"] - 14 - 10 * math.log10(1 + coupling * 3)
                p += 10 ** (lvl / 10) * it["duty"] * coupling
        idbm = 10 * math.log10(p) if p > 0 else -100
        util = min(99, round(8 + len(on_ap) * 14 + max(0, idbm + 95) * 1.6))
        spectrum.append(dict(ch=ch, freq=f, ap_count=len(on_ap), interference_dbm=round(idbm, 1),
                             util_pct=util, non_overlap=ch in NON_OVERLAP_24))
    # 5 GHz 分组概览
    spec5 = []
    for name, chans in CH5_GROUPS.items():
        on_ap = [a for a in src if a["channel"] in chans]
        it5 = [it for it in ifs if it["band"] in ("5", "6")]
        p = 10.0 ** (-100.0 / 10.0)
        for it in it5:
            p += 10 ** ((it["power"] - 16) / 10) * it["duty"] / len(chans)
        spec5.append(dict(group=name, channels=chans, ap_count=len(on_ap),
                          interference_dbm=round(10 * math.log10(p), 1),
                          util_pct=min(99, round(6 + len(on_ap) * 9 + max(0, 10 * math.log10(p) + 95) * 1.2))))
    # 同信道冲突对
    conflicts = []
    for i, a in enumerate(src):
        if a["channel"] > 14:
            continue
        for b in src[i + 1:]:
            if b["channel"] != a["channel"]:
                continue
            d = math.hypot(a["x"] - b["x"], a["y"] - b["y"]) / ppm
            if d < 32:
                conflicts.append(dict(a="AP-%s" % a["id"], b="AP-%s" % b["id"],
                                      channel=a["channel"], distance_m=round(d, 1),
                                      severity="高" if d < 15 else "中",
                                      advice="两 AP 同信道且间距 %.0f m，建议改到负载更低的非重叠信道或下调发射功率" % d))
    conflicts.sort(key=lambda x: x["distance_m"])
    # 2.4G 贪心重规划（图着色简化）
    assigned, plan = [], []
    for a in src:
        if a["channel"] > 14:
            continue
        score = {ch: 0.0 for ch in NON_OVERLAP_24}
        for b, nch in assigned:
            d = math.hypot(a["x"] - b["x"], a["y"] - b["y"]) / ppm
            if d < 32:
                score[nch] += (32 - d) / 32 * 10
        for ch in NON_OVERLAP_24:
            score[ch] += spectrum[ch - 1]["util_pct"] * .04
        best = min(score, key=score.get)
        assigned.append((a, best))
        if best != a["channel"]:
            plan.append(dict(ap="AP-%s" % a["id"], model=a.get("model", "-"),
                             old=a["channel"], new=best,
                             reason="降低同信道重叠（冲突图贪心着色）"))
    # 干扰源影响评估
    ifs_out = []
    for it in ifs:
        affected = [s["ch"] for s in spectrum if s["interference_dbm"] > -88] if it["band"] == "2.4" else []
        near_ap = min((math.hypot(it["x"] - a["x"], it["y"] - a["y"]) / ppm for a in src), default=99)
        sev = "高" if it["duty"] > .6 and near_ap < 12 else "中" if it["duty"] > .3 else "低"
        ifs_out.append(dict(it, affected_channels=affected[:8], severity=sev,
                            nearest_ap_m=round(near_ap, 1)))
    return dict(spectrum_24=spectrum, spectrum_5=spec5, conflicts=conflicts,
                channel_plan=plan, interferers=ifs_out,
                library=[t[0] for t in INTERFERER_LIBRARY],
                summary=dict(interferer_count=len(ifs), conflict_pairs=len(conflicts),
                             ssid=(ssid or {}).get("name", "全部 SSID"),
                             ssid_ap_count=len(src), band=band,
                             high_severity=sum(1 for i in ifs_out if i["severity"] == "高"),
                             suggested_changes=len(plan),
                             max_util_pct=max(s["util_pct"] for s in spectrum),
                             worst_channel=max(spectrum, key=lambda s: s["util_pct"])["ch"]))


# ---------------- 故障排查规则库 ----------------
# cond: 证据条件; w: 基础权重; symptoms: 相关症状加成
RULES = [
    dict(id="R01", name="覆盖空洞 / 弱信号", w=90, symptoms=["局部无信号", "时断时续", "网速慢"],
         cond=lambda e: e["rssi"] < -72,
         evidence=lambda e: "实测 RSSI %.0f dBm 低于 -72 dBm 语音/视频门限" % e["rssi"],
         action="核算该区域 AP 间距与墙体衰减；增补 AP 或调整安装位置/天线方向；复核发射功率是否被 RRM 压低。",
         retest="复测该点 RSSI ≥ -67 dBm 且 SNR ≥ 25 dB。"),
    dict(id="R02", name="信噪比不足 / 非 Wi-Fi 干扰", w=80, symptoms=["网速慢", "时断时续", "连不上"],
         cond=lambda e: e["snr"] < 20,
         evidence=lambda e: "SNR %.0f dB 偏低，噪声底 %.0f dBm" % (e["snr"], e["noise"]),
         action="用频谱扫描排查微波炉/蓝牙/无线摄像头/变频器等干扰源；避开受干扰信道；必要时降低带宽至 20/40 MHz 提升抗噪。",
         retest="复测 SNR ≥ 25 dB，空口误码/重传率下降。"),
    dict(id="R03", name="同信道干扰 / 信道规划冲突", w=85, symptoms=["网速慢", "时断时续", "漫游掉线"],
         cond=lambda e: e["cochannel_aps"] >= 3 or e["overlap_pct"] > 30,
         evidence=lambda e: "同信道可见 AP %d 个，强重叠区域占比 %.0f%%" % (e["cochannel_aps"], e["overlap_pct"]),
         action="基于 AP 冲突图做 2.4G 1/6/11 与 5G 非重叠信道重规划；关闭低效低速集；避免 AP 功率过大造成 CCI。",
         retest="复测同信道重叠占比 < 15%，空口利用率下降。"),
    dict(id="R04", name="信道拥塞 / 空口过载", w=88, symptoms=["网速慢", "连不上", "时断时续"],
         cond=lambda e: e["channel_util"] > 65,
         evidence=lambda e: "信道利用率 %.0f%% 超过 65%% 预警线" % e["channel_util"],
         action="扩容 AP 或启用 5G/6G 频段引导、负载均衡；限制低速客户端；检查单 AP 关联数是否超限。",
         retest="复测忙时信道利用率 < 50%，单用户吞吐回升。"),
    dict(id="R05", name="高重传 / 隐藏节点或速率塌陷", w=72, symptoms=["网速慢", "时断时续"],
         cond=lambda e: e["retry_rate"] > 20,
         evidence=lambda e: "重传率 %.0f%% 偏高" % e["retry_rate"],
         action="检查 RTS/CTS、隐藏节点与终端距离；排查 802.11b/g 保护机制；核对 MCS 速率阶梯与终端能力。",
         retest="复测重传率 < 10%。"),
    dict(id="R06", name="DHCP 地址池 / 中继异常", w=84, symptoms=["连不上", "认证失败", "时断时续"],
         cond=lambda e: e["dhcp_fail"] > 3,
         evidence=lambda e: "DHCP 失败率 %.0f%%" % e["dhcp_fail"],
         action="核查地址池利用率、租期、Option 43 / Relay、VLAN 透传与用户网关；确认漫游后是否重复续租。",
         retest="复测 20 次关联全部正常获取地址，租期与网关正确。"),
    dict(id="R07", name="802.1X / RADIUS 认证链路异常", w=86, symptoms=["认证失败", "连不上", "漫游掉线"],
         cond=lambda e: e["auth_fail"] > 3,
         evidence=lambda e: "认证失败率 %.0f%%" % e["auth_fail"],
         action="核查 RADIUS 可达性、共享密钥、证书有效期、EAP 类型与计费端口；检查 WLC/AP 与 AAA 的时间同步。",
         retest="复测认证成功率 ≥ 99%，抓包确认 EAP 交互完整。"),
    dict(id="R08", name="漫游粘性 / 未启用 802.11k/v/r", w=78, symptoms=["漫游掉线", "时断时续"],
         cond=lambda e: e["roam_fail"] > 3 or (e["rssi"] > -67 and e["symptom"] == "漫游掉线"),
         evidence=lambda e: "漫游失败率 %.0f%%，当前 RSSI %.0f dBm（信号良好却掉线）" % (e["roam_fail"], e["rssi"]),
         action="开启 802.11k/v/r 与 PMK 缓存/OKC；调整最小接入 RSSI 与漫游阈值；统一 SSID/安全套件与 VLAN。",
         retest="复测跨 AP 漫游切换 < 50 ms，语音不中断。"),
    dict(id="R09", name="发射功率失衡 / 远近效应", w=66, symptoms=["网速慢", "连不上", "漫游掉线"],
         cond=lambda e: e["ap_tx_power"] > 23 or (e["client_count"] > 40 and e["ap_count"] <= 2),
         evidence=lambda e: "AP 发射功率 %.0f dBm，单 AP 客户端 %d 台" % (e["ap_tx_power"], e["client_count"]),
         action="下调 2.4G 功率并让同楼层 AP 功率档位一致；启用频段引导与负载均衡，避免终端被远端大功率 AP 吸住。",
         retest="复测上下行对称，近远端 RSSI 差值收敛。"),
    dict(id="R10", name="DFS 雷达避让引发信道切换", w=70, symptoms=["时断时续", "漫游掉线", "连不上"],
         cond=lambda e: e["dfs_event"] > 0,
         evidence=lambda e: "近期 DFS 事件 %d 次" % e["dfs_event"],
         action="改用非 DFS 信道或规划固定信道；确认客户端 6 GHz/DFS 支持能力；检查雷达误检日志。",
         retest="复测 24 h 内无 DFS 强制切换。"),
    dict(id="R11", name="2.4 GHz 频段饱和 / 未做频段引导", w=68, symptoms=["网速慢", "连不上"],
         cond=lambda e: e["band"] == "2.4" and e["client_count"] > 25,
         evidence=lambda e: "2.4 GHz 单频段承载 %d 台客户端" % e["client_count"],
         action="启用 band steering / 5G 优先；2.4G 仅用 20 MHz 与 1/6/11；评估关闭部分 2.4G 射频降低 CCI。",
         retest="复测 5G 关联占比提升，2.4G 利用率下降。"),
    dict(id="R12", name="老旧设备能力受限（EOL / Wi-Fi 4 及更早）", w=60,
         symptoms=["网速慢", "漫游掉线", "时断时续", "连不上"],
         cond=lambda e: e["legacy"] or e["ap_gen"] in ("Wi-Fi 4", "Wi-Fi 3", "Wi-Fi 2", "Wi-Fi 1"),
         evidence=lambda e: "AP 世代 %s，型号能力层级 %s" % (e["ap_gen"], "老旧/受限" if e["legacy"] else "在售"),
         action="核对固件是否已到厂商末版（如 AireOS 8.5 / FortiOS 6.0.6 上限）；老旧 AP 不参与 5/6 GHz 与 k/v/r 优化；纳入替换优先级清单。",
         retest="确认旧设备仅承担低密覆盖，关键区域完成替换后复测。"),
]

EVIDENCE_DEFAULTS = dict(
    rssi=-62, snr=28, noise=-95, channel_util=40, client_count=18,
    ap_count=6, cochannel_aps=1, overlap_pct=10, retry_rate=8,
    dhcp_fail=0, auth_fail=0, roam_fail=0, ap_tx_power=20,
    dfs_event=0, band="2.4", symptom="网速慢", legacy=False, ap_gen="Wi-Fi 6")


def diagnose_engine(req):
    e = dict(EVIDENCE_DEFAULTS)
    for k in e:
        if k in req and req[k] is not None and req[k] != "":
            try:
                e[k] = type(e[k])(req[k])
            except Exception:
                e[k] = req[k]
    symptom = str(e.get("symptom", ""))
    hits = []
    for r in RULES:
        try:
            ok = bool(r["cond"](e))
        except Exception:
            ok = False
        if not ok:
            continue
        w = float(r["w"])
        if symptom in r["symptoms"]:
            w *= 1.35
        # 证据强度：越偏离门限越可信
        w *= 1.0 + min(0.25, abs(float(e.get("rssi", -62)) + 67) / 200.0)
        hits.append(dict(id=r["id"], name=r["name"], score=round(w, 1),
                         evidence=r["evidence"](e), action=r["action"], retest=r["retest"]))
    hits.sort(key=lambda x: -x["score"])
    total = sum(h["score"] for h in hits) or 1.0
    for h in hits:
        h["confidence"] = round(100.0 * h["score"] / total, 1)
    s = ssid_get(req.get("ssid_id")) or ssid_active()
    return {
        "symptom": symptom,
        "ssid": (s or {}).get("name", "全部 SSID"),
        "ssid_band": (s or {}).get("band", "双频"),
        "evidence": {k: e[k] for k in EVIDENCE_DEFAULTS},
        "top": hits[:4],
        "all": hits,
        "note": "规则推理演示：当前未接现网数据，结论须结合现场证据与设备日志复核；多证据加权，单一规则命中不作为定论。"
                + ("　当前分析对象：SSID %s（%s / VLAN %s），核查时请确认该 SSID 的广播、认证与 VLAN 配置。"
                   % ((s or {}).get("name", "-"), (s or {}).get("band", "-"), (s or {}).get("vlan", "-")) if s else ""),
    }


# ---------------- WLC 离线知识库（官方文档提炼，运行时零外网请求） ----------------
import re

WLC_KB = {"counts": {}, "doc_library": [], "playbooks": [], "error_codes": {},
          "commands": [], "phrase_templates": [], "offline": True}
try:
    with open(os.path.join(DATA_DIR, "wlc_offline_kb.json"), "r", encoding="utf-8") as f:
        WLC_KB = json.load(f)
except Exception as e:
    log("  ! WLC 离线知识库加载失败: %s" % e)


def wlc_kb_overview():
    return {
        "offline": True,
        "schema_version": WLC_KB.get("schema_version", "-"),
        "generated_at": WLC_KB.get("generated_at", "-"),
        "counts": WLC_KB.get("counts", {}),
        "severity_legend": WLC_KB.get("severity_legend", {}),
        "severity_cisco": WLC_KB.get("severity_cisco", {}),
        "offline_notice": WLC_KB.get("offline_notice", ""),
        "license_notice": WLC_KB.get("license_notice", ""),
        "sources_note": WLC_KB.get("sources_note", ""),
        "docs": WLC_KB.get("doc_library", []),
        "phrase_templates": WLC_KB.get("phrase_templates", []),
        "commands": WLC_KB.get("commands", []),
        "sops": WLC_KB.get("sop_library", []),
        "doc_facets": WLC_KB.get("doc_facets", {}),
        "playbook_index": [dict(id=p["id"], title=p["title"], vendors=p["vendors"],
                                category=p["category"], severity=p["severity"],
                                symptom=p["symptom"])
                           for p in WLC_KB.get("playbooks", [])],
    }


def _code_hay(r):
    return " ".join(str(r.get(k, "")) for k in
                    ("code", "name", "zh", "explanation", "action", "detail", "hint_zh", "severity"))


def wlc_code_search(q="", group="", limit=60):
    q = str(q or "").strip().lower()
    groups = WLC_KB.get("error_codes", {})
    out = []
    for gkey, g in groups.items():
        if group and group != gkey:
            continue
        for r in g.get("records", []):
            if q and q not in _code_hay(r).lower():
                continue
            item = dict(r)
            item["group"] = gkey
            item["group_vendor"] = g.get("vendor", "")
            item["source_doc"] = g.get("source_doc", "")
            out.append(item)
            if len(out) >= limit:
                return {"total": len(out), "truncated": True, "records": out,
                        "note": "仅返回前 %d 条，请缩小关键词" % limit}
    return {"total": len(out), "truncated": False, "records": out, "note": ""}


def wlc_commands(q="", limit=80):
    q = str(q or "").strip().lower()
    out = [c for c in WLC_KB.get("commands", [])
           if not q or q in " ".join(str(v) for v in c.values()).lower()]
    return {"total": len(out), "records": out[:limit]}


def _doc_hay(x):
    return " ".join(str(x.get(k, "") or "") for k in
                    ("id", "vendor", "title", "version", "url", "kind", "summary_zh",
                     "topic", "scene", "keywords", "commands_zh", "action_zh",
                     "verify_status")).lower()


def wlc_doc_search(q="", vendor="", topic="", status="", limit=200):
    """离线文献库检索：多关键词 AND 匹配全文提炼，按厂商/专题/核实状态过滤。"""
    terms = [t for t in str(q or "").strip().lower().split() if t]
    all_docs = WLC_KB.get("doc_library", [])
    out = []
    for x in all_docs:
        if vendor and x.get("vendor") != vendor:
            continue
        if topic and (x.get("topic") or "未分类") != topic:
            continue
        if status and (x.get("verify_status") or "public-verified") != status:
            continue
        hay = _doc_hay(x)
        if terms and not all(t in hay for t in terms):
            continue
        out.append(x)
    facets = WLC_KB.get("doc_facets", {}) or {}
    if not facets:
        from collections import Counter as _C
        facets = dict(vendors=dict(_C(x.get("vendor", "") for x in all_docs)),
                      topics=dict(_C(x.get("topic") or "未分类" for x in all_docs)),
                      verify_status=dict(_C(x.get("verify_status") or "public-verified"
                                            for x in all_docs)))
    return {"total": len(out), "truncated": len(out) > limit,
            "records": out[:limit], "facets": facets,
            "note": "结果已按全部关键词 AND 过滤；数据全部来自内置离线知识包，运行时零外网请求"}


def wlc_sop_list():
    sops = WLC_KB.get("sop_library", [])
    by_id = {x.get("id"): x for x in WLC_KB.get("doc_library", [])}
    out = []
    for s in sops:
        item = dict(s)
        item["ref_docs"] = [dict(id=r, title=by_id.get(r, {}).get("title", r),
                                 vendor=by_id.get(r, {}).get("vendor", ""))
                            for r in s.get("source_docs", [])]
        out.append(item)
    return {"total": len(out), "records": out}


def _find_codes(group, pred):
    g = WLC_KB.get("error_codes", {}).get(group, {})
    out = []
    for r in g.get("records", []):
        if pred(r):
            item = dict(r)
            item["group"] = group
            item["group_vendor"] = g.get("vendor", "")
            item["source_doc"] = g.get("source_doc", "")
            out.append(item)
    return out


def wlc_analyze(text, vendor=""):
    """离线分析粘贴的日志 / 配置 / 抓包文本：命中官方码表与诊断剧本，输出话术与处置步骤。"""
    text = str(text or "")[:200000]
    up = text.upper()
    up2 = re.sub(r"REASON\s*[:='\"]+\s*(\d{1,4})",
                 lambda m: "REASON " + m.group(1) + " REASONCODE" + m.group(1), up)
    found = {}
    matched_codes = []
    extracted_numbers = set()

    def kw_hit(kw):
        k = str(kw).upper()
        if k.isascii() and len(k) <= 4 and k.isalnum():
            return re.search(r"\b" + re.escape(k) + r"\b", up2) is not None
        return k in up2

    def add(group, items, how):
        for r in items:
            key = group + "|" + str(r.get("code", ""))
            if key in found:
                continue
            found[key] = True
            r = dict(r)
            r["matched_by"] = how
            r["group"] = group
            g = WLC_KB.get("error_codes", {}).get(group, {})
            r["group_vendor"] = g.get("vendor", "")
            r["source_doc"] = g.get("source_doc", "")
            matched_codes.append(r)

    cisco_all = WLC_KB.get("error_codes", {}).get("cisco_9800_client_delete", {}).get("records", [])
    known_cisco = {r["code"] for r in cisco_all}
    add("cisco_9800_client_delete", [r for r in cisco_all if r["code"] in up], "官方原因码原文命中")
    named = set(re.findall(r"\b(?:CO_CLIENT_DELETE_REASON|MN)_[A-Z0-9_]+\b", up))
    unknown_cisco = [dict(code=c, name="官方表未收录", zh="疑似不同软件版本的私有原因码",
                          explanation="", action="以设备实际软件版本的官方文档为准",
                          hint_zh="该码未出现在内置的 270 条 DevNet 表中，请用设备版本核对",
                          category="其他", attention=2, severity_zh="需现场核对")
                     for c in named if c not in known_cisco]
    add("cisco_9800_client_delete", unknown_cisco, "日志提取（未收录，需现场核对）")

    nums = set(re.findall(r"(?:error\s*code|错误码|code)\s*[:\"'=]*\s*(\d{1,4})\b", text, re.I))
    extracted_numbers |= nums
    if nums:
        add("ruckus_sz_api", _find_codes("ruckus_sz_api", lambda r: str(r.get("code")) in nums), "日志错误码命中")
        add("h3c_community", _find_codes("h3c_community", lambda r: str(r.get("code")) in nums), "社区资料码命中")
    reasons = set(re.findall(r"(?:reason\s*code|reason|原因码|da_reason)\s*[:='\"]*\s*(\d{1,4})\b", text, re.I))
    extracted_numbers |= reasons
    if reasons:
        add("ieee80211_reason", _find_codes("ieee80211_reason", lambda r: str(r.get("code")) in reasons), "802.11 原因码命中")
    fails = set(re.findall(r"last\s+failure\s*[:=]?\s*(\d{1,4})\b", text, re.I))
    extracted_numbers |= fails
    if fails:
        add("fortigate_wtp_failure", _find_codes("fortigate_wtp_failure", lambda r: str(r.get("code")) in fails), "WTP last failure 命中")

    # 关键词兜底：文本里出现码表条目名称但没有结构化码值时也能命中
    if not matched_codes:
        for gkey in ("fortigate_wtp_failure", "h3c_community"):
            add(gkey, _find_codes(gkey, lambda r: str(r.get("name", ""))[:18].upper() in up
                                  or str(r.get("zh", "")) in text), "关键词命中")

    matched_codes = matched_codes[:40]
    hit_code_values = {str(r.get("code", "")).upper() for r in matched_codes}

    scored = []
    for p in WLC_KB.get("playbooks", []):
        score = 0
        for kw in p.get("keywords", []):
            if kw_hit(kw):
                score += max(2, min(9, len(str(kw)) // 3))
        for c in p.get("related_codes", []):
            cs = str(c).upper()
            if len(cs) <= 4 and cs.isdigit():
                if cs in extracted_numbers or cs in hit_code_values:
                    score += 18
            elif cs in named or cs in hit_code_values:
                score += 18
        for r in matched_codes:
            if r.get("category") == p.get("category"):
                score += 2
        if vendor and (vendor in p.get("vendors", []) or "通用" in p.get("vendors", [])):
            score += 3
        score *= {"critical": 1.30, "high": 1.15, "medium": 1.0,
                  "low": 0.9, "info": 0.85}.get(p.get("severity"), 1.0)
        if score > 0:
            item = dict(p)
            item["score"] = round(score, 1)
            scored.append(item)
    scored.sort(key=lambda x: -x["score"])
    cutoff = max(2.0, (scored[0]["score"] * 0.25) if scored else 0)
    top = [p for p in scored if p["score"] >= cutoff][:5]
    for i, p in enumerate(top):
        p["rank"] = i + 1

    doc_ids = []
    for p in top:
        for d in p.get("doc_refs", []):
            if d not in doc_ids:
                doc_ids.append(d)
    for r in matched_codes:
        d = r.get("source_doc")
        if d and d not in doc_ids:
            doc_ids.append(d)
    docs = [d for d in WLC_KB.get("doc_library", []) if d.get("id") in doc_ids]

    if top:
        t = top[0]
        summary = ("最可能：%s。%s 首选动作：%s。" %
                   (t["title"], t["conclusion"], t["steps"][0]["action"] if t.get("steps") else "补充证据"))
        phrases = t.get("phrases", {})
    else:
        summary = ("未命中内置的诊断剧本。可尝试粘贴含下列内容的原文："
                   "Cisco 的 CO_CLIENT_DELETE_REASON_* 原因码、FortiGate 的 last failure / wlac 输出、"
                   "802.11 reason code、Ruckus 私有错误码、或 WLAN/Policy Profile 配置段落。")
        phrases = {}

    return {
        "offline": True, "text_len": len(text), "vendor": vendor,
        "matched_codes": matched_codes, "matched_code_count": len(matched_codes),
        "top_playbooks": top, "playbook_hit_count": len(scored),
        "phrases": phrases, "phrase_templates": WLC_KB.get("phrase_templates", []),
        "docs": docs, "summary": summary,
        "note": ("全部结论来自程序内置知识包，运行时未访问外网。官方英文 Explanation / Action 为原文，"
                 "中文提示为自动归纳；社区资料与「需现场核对」条目不得直接用于自动决策。"
                 "工具只读分析，不自动下发任何配置。"),
    }


# ---------------- 空口扫描与干扰评估（真实扫描，Windows Native WiFi） ----------------
# 数据来自 WlanGetNetworkBssList：SSID / BSSID / 信道 / 带宽 / RSSI / 加密 / BSS Load。
# 全程只读：WlanScan 触发一次被动+主动扫描，不连接、不改配置、不上传。
AIR = {"last": None, "at": 0.0, "busy": False, "error": ""}
AIR_TTL = 25.0     # 秒，避免前端连续刷新反复扫描


def air_scan(force=True, wait=5.0, point=""):
    now = time.time()
    if not force and AIR["last"] and (now - AIR["at"]) < AIR_TTL:
        out = dict(AIR["last"])
        out["cached"] = True
        out["age_sec"] = round(now - AIR["at"], 1)
        return out
    if AIR["busy"]:
        return {"ok": False, "error": "上一次扫描还在进行，请稍候", "cached": True,
                "bss": [], "analysis": None}
    AIR["busy"] = True
    try:
        try:
            import wlan_scan as WS
            import air_quality as AQ
        except Exception as e:
            return {"ok": False, "error": "扫描模块加载失败：%s" % e, "bss": [], "analysis": None}
        raw = WS.scan_bss(wait_seconds=max(2.0, min(9.0, float(wait or 5.0))))
        if not raw.get("ok"):
            AIR["error"] = raw.get("error", "")
            return {"ok": False, "error": raw.get("error", "扫描失败"), "bss": [], "analysis": None}
        ana = AQ.analyze(raw.get("bss") or [], point or "本机所在位置")
        out = {
            "ok": True, "cached": False,
            "scanned_at": raw.get("scanned_at"),
            "interfaces": raw.get("interfaces") or [],
            "bss": raw.get("bss") or [],
            "analysis": ana,
            "point": point or "本机所在位置",
            "count": len(raw.get("bss") or []),
        }
        AIR.update(last=out, at=time.time(), error="")
        # 每次成功扫描落一行时间序列快照（本地 JSONL，供趋势回放与时段分析）
        try:
            DK, hist = _desktop()
            conn = (wlan_iface_state().get("connected") or {})
            hist.append(DK.history_record(out, conn))
        except Exception:
            pass
        return out
    except Exception as e:
        AIR["error"] = str(e)
        return {"ok": False, "error": "扫描异常：%s" % e, "bss": [], "analysis": None}
    finally:
        AIR["busy"] = False


def air_import(names):
    """把扫到的 SSID 批量加入 SSID 列表（已存在则跳过）。"""
    created, existed, skipped = [], [], []
    for raw in (names or []):
        name = str(raw or "").strip()
        if not name:
            continue
        if len(name) > 32:
            skipped.append(name[:32])
            continue
        if ssid_get(name=name):
            existed.append(name)
            continue
        sid = "ssid-scan-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
        bands = set()
        rssi = None
        sec = ""
        for b in (AIR["last"] or {}).get("bss", []):
            if b.get("ssid") == name and not b.get("hidden"):
                if b.get("band"):
                    bands.add(b["band"])
                if b.get("rssi") is not None and (rssi is None or b["rssi"] > rssi):
                    rssi = b["rssi"]
                sec = sec or (b.get("security") or "")
        band = "双频" if len(bands) > 1 else (next(iter(bands)) if bands else "双频")
        SSID_STATE["list"].append(dict(
            id=sid, name=name, purpose="空口扫描导入", band=band,
            security=sec or "扫描读取（未标注）", vlan=0, broadcast=True, enabled=True,
            min_rssi=-70, max_clients=60, ap_ids=[], source="scan",
            note="由真实空口扫描导入：本次采集点最强 RSSI %s dBm；如需按现网设计值使用请手工修订。"
                 % (rssi if rssi is not None else "-")))
        created.append(name)
    if created:
        save_ssids()
    return {"created": created, "existed": existed, "skipped": skipped,
            "state": ssid_public()}


# ---------------- 移动漫游监测（只读轮询，真实数据） ----------------
# 高频读 netsh 当前关联（1 秒级）+ 低频读系统 BSS 缓存（20 秒），判定漫游 / 掉线 / 粘滞。
# 全程只读：不连接、不断开、不修改无线配置、不联网。
ROAM = {"rec": None}


def _roam_rec():
    if ROAM["rec"] is None:
        import roam_monitor as RM
        import air_quality as AQ

        def _bss(trigger):
            import wlan_scan as WS
            return WS.scan_bss(wait_seconds=4.5 if trigger else 0.05, trigger=bool(trigger))

        ROAM["rec"] = RM.Recorder(wlan_iface_state, _bss, AQ.analyze)
    return ROAM["rec"]


def roam_start(req):
    try:
        rec = _roam_rec()
    except Exception as e:
        return {"ok": False, "error": "漫游监测模块加载失败：%s" % e}
    return rec.start(point=str(req.get("point") or ""),
                     poll=float(req.get("poll") or 1.0),
                     air_every=float(req.get("air_every") or 20.0))


def roam_force_scan():
    rec = _roam_rec()
    if not rec.running:
        return {"ok": False, "error": "请先开始监测"}
    rec._refresh_air(trigger=True)
    snap = rec.snapshot()
    snap["forced"] = True
    return snap


# ---------------- 现场勘测 / 智能审计 ----------------
# 对标 Ekahau Survey 的"实测打点 → 需求验收 → 模型校准 → 报告"链路，
# 以及 NetAlly AirMagnet AirWISE 的"自动问题清单 + 安全审计 + 信道规划"。
# 点位落 %APPDATA%/WirelessNetOptimizer/survey_points.json，全程只读、零外网。
SURVEY = {"store": None, "mod": None, "calibration": None}
EXTRAS = {"obj": None, "mod": None}
DESKTOP = {"mod": None, "hist": None}


def _desktop():
    if DESKTOP["mod"] is None:
        ensure_dirs()
        import desktop as DK
        DESKTOP["mod"] = DK
        DESKTOP["hist"] = DK.History(USER_DIR)
    return DESKTOP["mod"], DESKTOP["hist"]


def _extras():
    if EXTRAS["obj"] is None:
        ensure_dirs()
        import extras as EX
        EXTRAS["mod"] = EX
        EXTRAS["obj"] = EX.Extras(USER_DIR)
    return EXTRAS["obj"], EXTRAS["mod"]


def _audit_with_rules(sv, au):
    """审计规则自定义：逐条启停 + 严重度覆盖，重算健康分。"""
    try:
        ex, EX = _extras()
        return EX.apply_rules(au, ex.get("rules"), sv.recompute_audit)
    except Exception:
        return au


def _survey():
    if SURVEY["store"] is None:
        ensure_dirs()
        import survey as SV
        SURVEY["mod"] = SV
        SURVEY["store"] = SV.SurveyStore(USER_DIR)
        # 上次会话校准出的传播参数，重启程序后仍然沿用（用户可在勘测页重新校准）
        cached = SURVEY["store"].state.get("calibration")
        if isinstance(cached, dict) and cached.get("ok"):
            SURVEY["calibration"] = cached
    return SURVEY["store"], SURVEY["mod"]


def _air_snapshot():
    if not AIR["last"]:
        return {}
    out = dict(AIR["last"])
    out["cached"] = True
    out["age_sec"] = round(time.time() - AIR["at"], 1)
    return out


def _roam_snapshot():
    rec = ROAM.get("rec")
    if rec is None:
        return {}
    try:
        return rec.snapshot()
    except Exception:
        return {}


def _survey_context():
    store, SV = _survey()
    prof = SV.profile_of(store.state.get("profile"), store.state.get("custom"))
    pts = store.points()
    ev = SV.evaluate(pts, prof)
    air = _air_snapshot()
    roam = _roam_snapshot()
    sec = SV.security_findings(air, SSID_STATE["list"])
    plan = SV.channel_plan(air, SSID_STATE["list"])
    au = SV.audit(air, SSID_STATE["list"], ev, roam, plan, sec)
    au = _audit_with_rules(SV, au)
    return {"store": store, "sv": SV, "prof": prof, "points": pts, "eval": ev,
            "air": air, "roam": roam, "sec": sec, "plan": plan, "audit": au}


def survey_state():
    store, SV = _survey()
    prof = SV.profile_of(store.state.get("profile"), store.state.get("custom"))
    pts = store.points()
    air = _air_snapshot()
    return {
        "ok": True, "version": VERSION,
        "session": store.state.get("session") or "",
        "sessions": store.sessions(),
        "profile": prof,
        "profiles": [dict(SV.PROFILES[k]) for k in SV.PROFILE_ORDER] + [
            {"key": "custom", "name": "自定义阈值", "icon": "⚙",
             "desc": "在下方按现场 SLA 自行设定", "basis": "用户自定义"}],
        "layers": SV.LAYERS,
        "points": pts,
        "eval": SV.evaluate(pts, prof),
        "calibration": SURVEY.get("calibration") or store.state.get("calibration"),
        "air": {"available": bool(air), "scanned_at": air.get("scanned_at"),
                "age_sec": air.get("age_sec"), "count": air.get("count")},
        "floor": {"available": os.path.isfile(os.path.join(USER_DIR, "floorplan.png"))},
        "storage": os.path.join(USER_DIR, SV.SurveyStore.FILENAME),
    }


def survey_add_point(req):
    store, SV = _survey()
    air = _air_snapshot()
    # 「扫描并打点」：现场重新触发一次真实扫描，保证每个点位有独立快照，
    # 否则多点会共用上一份扫描结果，干扰指数/利用率被复制成同一个值。
    if bool(req.get("refresh_air")):
        fresh = air_scan(force=True, wait=float(req.get("wait") or 4.0),
                         point=str(req.get("label") or ""))
        if fresh.get("ok"):
            air = fresh
        elif not air:
            return {"ok": False, "error": "扫描失败：%s；可先在「空口扫描」页扫描一次，或取消『重新扫描』只按当前位置打点"
                                          % fresh.get("error", "未知原因")}
    connected = {}
    try:
        connected = wlan_iface_state().get("connected") or {}
    except Exception:
        pass
    roam = _roam_snapshot()
    pt = SV.capture_point(req, air, roam, connected, store)
    prof = SV.profile_of(store.state.get("profile"), store.state.get("custom"))
    return {"ok": True, "point": pt,
            "status": SV.compliance_of(pt, prof)[0],
            "eval": SV.evaluate(store.points(), prof),
            "air_scan": {"scanned_at": air.get("scanned_at"), "count": air.get("count"),
                         "refreshed": bool(req.get("refresh_air")), "ok": air.get("ok", True)}}


def survey_calibrate(req):
    """用勘测实测点反推路径损耗指数 n 与 1m 损耗 PL0，复用热力图工作台的传播先验。"""
    store, SV = _survey()
    pts = [p for p in store.points() if p.get("rssi") is not None]
    if len(pts) < 3:
        return {"ok": False, "error": "至少需要 3 个带 RSSI 的实测点才能校准（当前 %d 个）" % len(pts)}
    band = str(req.get("band") or "2.4")
    if band not in BAND_PARAM:
        band = "2.4"
    pl0_def, n_def = BAND_PARAM[band]
    ppm = max(0.5, float(req.get("ppm") or 10.0))
    walls = prep_walls(req.get("walls"))
    aps = prep_aps(req.get("aps"), band)
    if not aps:
        return {"ok": False, "error": "图纸上没有可用 AP，请先在「热力图」工作台放置 AP 并保存，或把 AP 列表传过来"}

    def predict_fn(n, pl0):
        return [max(predict_at(float(p["x"]), float(p["y"]), a, walls, pl0, n, ppm) for a in aps)
                for p in pts]

    out = SV.calibrate(pts, predict_fn, n_def, pl0_def)
    if out.get("ok"):
        out["band"] = band
        out["applied"] = False
        SURVEY["calibration"] = out
        store.state["calibration"] = out
        store.save()
    return out


def audit_state(refresh=False):
    if refresh:
        air_scan(force=True, wait=4.5, point="智能审计采集点")
    c = _survey_context()
    au, plan, sec, air = c["audit"], c["plan"], c["sec"], c["air"]
    return {
        "ok": True, "generated_at": au.get("generated_at"),
        "audit": au, "plan": plan, "security": sec,
        "eval": c["eval"],
        "air": {"available": bool(air), "scanned_at": air.get("scanned_at"),
                "age_sec": air.get("age_sec"), "count": air.get("count"),
                "point": air.get("point"), "rollup": (air.get("analysis") or {}).get("rollup") or {}},
        "session": c["store"].state.get("session") or "",
        "version": VERSION,
    }


def survey_report(sessions=None, diff_names=None):
    """生成单文件离线报告。可合订多个楼层 / 会话，并附优化前后对比章节。"""
    c = _survey_context()
    ex, EX = _extras()
    sections = []
    if sessions:
        allpoints = c["store"].state.get("points") or []
        for name in sessions[:20]:
            sp = [p for p in allpoints if (p.get("session") or "未命名会话") == name]
            e = c["sv"].evaluate(sp, c["prof"])
            st = e.get("stats") or {}
            sections.append({"session": name, "total": e.get("total", 0),
                             "pass": e.get("pass", 0), "edge": e.get("edge", 0),
                             "fail": e.get("fail", 0), "pass_rate": e.get("pass_rate"),
                             "rssi_min": st.get("rssi_min"), "sinr_avg": st.get("sinr_avg"),
                             "index_avg": st.get("index_avg")})
    diff = {}
    if diff_names and len(diff_names) == 2 and all(diff_names):
        diff = ex.snapshot_diff(diff_names[0], diff_names[1])
        if not diff.get("ok"):
            diff = {}
    return c["sv"].report_html({
        "store": c["store"].state, "eval": c["eval"], "audit": c["audit"],
        "plan": c["plan"], "sec": c["sec"],
        "calib": SURVEY.get("calibration") or c["store"].state.get("calibration") or {},
        "air": c["air"], "version": VERSION,
        "brand": dict(ex.get("report")), "sections": sections, "diff": diff,
    })


def report_docx_bytes(sessions=None, diff_names=None):
    """离线 .docx 报告：OOXML 由标准库直接生成，不依赖 Word / 浏览器 / 第三方库。"""
    c = _survey_context()
    ex, EX = _extras()
    DK, _hist = _desktop()
    sections = []
    if sessions:
        allpoints = c["store"].state.get("points") or []
        for name in sessions[:20]:
            sp = [p for p in allpoints if (p.get("session") or "未命名会话") == name]
            e = c["sv"].evaluate(sp, c["prof"])
            s = e.get("stats") or {}
            sections.append({"session": name, "total": e.get("total", 0),
                             "pass": e.get("pass", 0), "edge": e.get("edge", 0),
                             "fail": e.get("fail", 0), "pass_rate": e.get("pass_rate"),
                             "rssi_min": s.get("rssi_min"), "sinr_avg": s.get("sinr_avg")})
    diff = {}
    if diff_names and len(diff_names) == 2 and all(diff_names):
        diff = ex.snapshot_diff(diff_names[0], diff_names[1])
        if not diff.get("ok"):
            diff = {}
    return DK.report_docx({
        "brand": dict(ex.get("report")), "version": VERSION,
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "store": c["store"].state, "eval": c["eval"], "audit": c["audit"],
        "plan": c["plan"], "sec": c["sec"], "air": c["air"],
        "calib": SURVEY.get("calibration") or c["store"].state.get("calibration") or {},
        "sections": sections, "diff": diff,
        "boundary": [
            "普通笔记本网卡只解析 802.11 Beacon/Probe，不提供原始 IQ/FFT，无法做非 Wi-Fi 干扰源识别与物理定位。",
            "隐藏 SSID 的名称在空口不传输，无法还原；BSSID 厂商由内置 OUI 前缀推断，随机私有 MAC 不适用。",
            "漫游切换耗时上限等于采样间隔，不是空口抓包级时延；主动扫描会让网卡短暂切信道。",
            "需求阈值是公开工程经验值，非厂商认证标准；审计是确定性规则库，不是机器学习结论。",
            "未接控制器 / SNMP，无法读取真实发射功率、关联终端数与历史告警；空口无法判断 AP 是否接入内部有线网。",
            "本工具全程只读、不发包测速、不联网、不下发配置；示意草图 / DXF 不是测绘成果，墙体损耗取经验值。",
        ],
    })


def _reset_in_memory():
    """备份恢复后清空内存缓存，让各模块下次请求时重新读盘。"""
    EXTRAS.update(obj=None, mod=None)
    SURVEY.update(store=None, mod=None, calibration=None)
    DESKTOP.update(mod=None, hist=None)
    AIR.update(last=None, at=0.0, error="")
    load_ssids()


def survey_import_csv(req):
    """点位 CSV 批量导入：兼容本工具导出的 CSV 与同结构的现场记录。"""
    ex, EX = _extras()
    store, SV = _survey()
    pts, errors, skipped = EX.parse_points_csv(req.get("csv") or "")
    if not pts:
        return {"ok": False, "error": "没有可导入的点位：" + ("；".join(errors) if errors else "数据行缺少坐标"),
                "errors": errors, "skipped": skipped}
    now = time.time()
    for i, p in enumerate(pts):
        p.setdefault("session", store.state.get("session") or "")
        p.setdefault("at", now + i * 0.001)
        p.setdefault("at_text", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p["at"])))
        store.add(p)
    return {"ok": True, "imported": len(pts), "errors": errors, "skipped": skipped,
            "state": survey_state()}


def survey_snapshot(req):
    """基线快照：保存当前验收结论 / 列出 / 删除 / 对比两次快照。"""
    ex, EX = _extras()
    action = str(req.get("action") or "list")
    if action == "save":
        c = _survey_context()
        out = ex.snapshot_save(req.get("name"), c["store"].state.get("session"),
                               c["eval"], c["audit"], c["points"])
    elif action == "delete":
        out = ex.snapshot_delete(req.get("name"))
    elif action == "diff":
        out = ex.snapshot_diff(str(req.get("before") or ""), str(req.get("after") or ""))
    else:
        out = {"ok": True, "list": ex.snapshot_list()}
    if action in ("save", "delete"):
        out["extras"] = ex.state()
    return out


def extras_state():
    ex, EX = _extras()
    return ex.state()


def extras_set(req):
    ex, EX = _extras()
    target = str(req.get("target") or "")
    if target == "report":
        out = ex.set_report(req.get("data") or {})
    elif target == "floors":
        d = req.get("data") or {}
        if d.get("action") == "current":
            out = ex.floor_set_current(d.get("name"))
        else:
            out = ex.floor_add(d.get("name"), d.get("width_m"), d.get("note"))
    elif target == "rules":
        out = ex.set_rules(req.get("data") or {})
    else:
        out = {"ok": False, "error": "未知设置项"}
    if out.get("ok"):
        out["extras"] = ex.state()
    return out


def survey_csv():
    import csv
    import io
    store, SV = _survey()
    prof = SV.profile_of(store.state.get("profile"), store.state.get("custom"))
    ev = SV.evaluate(store.points(), prof)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["点位", "会话", "坐标X", "坐标Y", "采集时间", "来源", "SSID", "BSSID",
                "RSSI(dBm)", "SNR(dB)", "SINR(dB)", "频段", "信道", "带宽(MHz)",
                "加密", "干扰指数", "信道等级", "可见射频", "利用率(%)", "空口BSS数",
                "区域评分", "判定", "不达标原因", "扫描时间", "备注"])
    for p in store.points():
        st, reasons, _ = SV.compliance_of(p, prof)
        w.writerow([p.get("label"), p.get("session"), p.get("x"), p.get("y"),
                    p.get("at_text"), p.get("source"), p.get("ssid"), p.get("bssid"),
                    p.get("rssi"), p.get("snr"), p.get("sinr"), p.get("band"),
                    p.get("channel"), p.get("width_mhz"), p.get("security"),
                    p.get("interference_index"), p.get("channel_level"), p.get("visible_radios"),
                    p.get("utilization_pct"), p.get("bss_total"), p.get("overall_score"),
                    ["不达标", "边缘", "达标"][st], "；".join(reasons),
                    p.get("scanned_at"), p.get("note")])
    return buf.getvalue().encode("utf-8-sig")


# ---------------- HTTP ----------------
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = APP_NAME + "/" + VERSION

    def log_message(self, fmt, *args):
        pass  # 静默访问日志，仅保留启动横幅与异常

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

    def _file(self, path):
        if not os.path.isfile(path):
            return self._send(404, "Not Found: " + os.path.basename(path), "text/plain; charset=utf-8")
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))

    def _safe_name(self, name):
        name = os.path.basename(str(name or "")).replace("..", "")
        return "".join(c for c in name if c not in '\\/:*?"<>|')[:80]

    def do_GET(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        q = parse_qs(u.query)
        try:
            if p in ("/", "/index.html"):
                return self._file(os.path.join(WEB_DIR, "index.html"))
            if p.startswith("/static/"):
                return self._file(os.path.join(WEB_DIR, self._safe_name(p[len("/static/"):])))
            if p == "/plan.html":
                return self._file(os.path.join(DATA_DIR, "plan.html"))
            if p == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if p == "/api/health":
                return self._json({"ok": True, "version": VERSION, "models": len(CATALOG.get("records", []))})
            if p == "/api/stats":
                return self._json({"version": VERSION, "app_name": APP_NAME,
                                   "user_dir": USER_DIR, "stats": CATALOG.get("stats", {}),
                                   "catalog_version": CATALOG.get("catalog_version", "-"),
                                   "generated_at": CATALOG.get("generated_at", "-")})
            if p == "/api/catalog":
                return self._json(CATALOG)
            if p == "/api/wlc/kb":
                return self._json(wlc_kb_overview())
            if p == "/api/wlc/codes":
                return self._json(wlc_code_search(
                    q=(q.get("q") or [""])[0],
                    group=(q.get("group") or [""])[0],
                    limit=min(200, max(10, int((q.get("limit") or [60])[0])))))
            if p == "/api/wlc/commands":
                return self._json(wlc_commands(q=(q.get("q") or [""])[0]))
            if p == "/api/wlc/docs":
                return self._json(wlc_doc_search(
                    q=(q.get("q") or [""])[0],
                    vendor=(q.get("vendor") or [""])[0],
                    topic=(q.get("topic") or [""])[0],
                    status=(q.get("status") or [""])[0],
                    limit=min(400, max(10, int((q.get("limit") or [200])[0])))))
            if p == "/api/wlc/sop":
                return self._json(wlc_sop_list())
            if p == "/api/ssid/list":
                return self._json(ssid_public())
            if p == "/api/air/scan":
                force = str((q.get("cached") or ["0"])[0]) not in ("1", "true")
                wait = float((q.get("wait") or ["5"])[0])
                return self._json(air_scan(force=force, wait=wait,
                                           point=(q.get("point") or [""])[0]))
            if p == "/api/air/last":
                if not AIR["last"]:
                    return self._json({"ok": False, "error": "尚未扫描", "bss": [], "analysis": None})
                out = dict(AIR["last"]); out["cached"] = True
                out["age_sec"] = round(time.time() - AIR["at"], 1)
                return self._json(out)
            if p == "/api/wlan/local":
                return self._json(wlan_local_scan())
            if p == "/api/survey/state":
                return self._json(survey_state())
            if p == "/api/survey/heatmap":
                store, SV = _survey()
                field = str((q.get("field") or ["rssi"])[0])
                if field not in SV.LAYERS:
                    field = "rssi"
                prof = SV.profile_of(store.state.get("profile"), store.state.get("custom"))
                return self._json(SV.idw_grid(
                    store.points(), field, prof,
                    cols=int((q.get("cols") or [100])[0]),
                    rows=int((q.get("rows") or [70])[0]),
                    W=float((q.get("width") or [1000])[0]),
                    H=float((q.get("height") or [700])[0])))
            if p == "/api/survey/report":
                ex, EX = _extras()
                sessions = [s for s in str((q.get("sessions") or [""])[0]).split("|") if s]
                dn = str((q.get("diff") or [""])[0]).split("|")
                return self._send(200, survey_report(sessions, dn), "text/html; charset=utf-8",
                                  {"Content-Disposition": 'inline; filename="survey-report.html"'})
            if p == "/api/survey/template.csv":
                ex, EX = _extras()
                data = ("\ufeff" + EX.csv_template().lstrip("\ufeff")).encode("utf-8")
                return self._send(200, data, "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="survey-points-template.csv"'})
            if p == "/api/extras/state":
                return self._json(extras_state())
            if p == "/api/local/diagnostics":
                DK, _hist = _desktop()
                return self._json(DK.local_diagnostics())
            if p == "/api/history/state":
                _DK, hist = _desktop()
                return self._json(hist.state(int((q.get("limit") or ["400"])[0])))
            if p == "/api/history/export":
                _DK, hist = _desktop()
                fn = "air-history-%s.csv" % time.strftime("%Y%m%d-%H%M%S")
                return self._send(200, hist.csv_bytes(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="%s"' % fn})
            if p == "/api/report/docx":
                sessions = [s for s in str((q.get("sessions") or [""])[0]).split("|") if s]
                dn = str((q.get("diff") or [""])[0]).split("|")
                fn = "survey-report-%s.docx" % time.strftime("%Y%m%d-%H%M%S")
                return self._send(200, report_docx_bytes(sessions, dn),
                                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                  {"Content-Disposition": 'attachment; filename="%s"' % fn})
            if p == "/api/backup/export":
                DK, _hist = _desktop()
                data, n = DK.backup_bytes(USER_DIR)
                fn = "WirelessNetOptimizer-backup-%s.zip" % time.strftime("%Y%m%d-%H%M%S")
                return self._send(200, data, "application/zip",
                                  {"Content-Disposition": 'attachment; filename="%s"' % fn,
                                   "X-Backup-Files": str(n)})
            if p == "/api/survey/export":
                fn = "survey-%s.csv" % time.strftime("%Y%m%d-%H%M%S")
                return self._send(200, survey_csv(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="%s"' % fn})
            if p == "/api/audit/state":
                return self._json(audit_state(refresh=False))
            if p == "/api/roam/state":
                return self._json(_roam_rec().snapshot())
            if p == "/api/roam/export":
                rec = _roam_rec()
                data = rec.to_csv().encode("utf-8")
                fn = "roam-%s.csv" % time.strftime("%Y%m%d-%H%M%S")
                return self._send(200, data, "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="%s"' % fn})
            if p == "/api/live/snapshot":
                return self._json(live_snapshot())
            if p == "/api/interference":
                return self._json(interference_analysis(ppm=float((q.get("ppm") or [10])[0]),
                                                        band=(q.get("band") or ["2.4"])[0],
                                                        ssid_id=(q.get("ssid_id") or [None])[0]))
            if p == "/api/project/list":
                files = [f[:-5] for f in os.listdir(PROJECT_DIR) if f.endswith(".json")]
                return self._json({"projects": sorted(files)})
            if p == "/api/project/load":
                name = self._safe_name((q.get("name") or [""])[0])
                path = os.path.join(PROJECT_DIR, name + ".json")
                if not os.path.isfile(path):
                    return self._json({"error": "项目不存在"}, 404)
                with open(path, "r", encoding="utf-8") as f:
                    return self._json(json.load(f))
            if p == "/api/floorplan":
                fp = os.path.join(USER_DIR, "floorplan.png")
                if not os.path.isfile(fp):
                    # 无图纸时返回 204 让 <img> 走 onerror 画网格底图，避免控制台刷 404
                    return self._send(204, b"", "image/png")
                return self._file(fp)
            return self._send(404, "Not Found", "text/plain; charset=utf-8")
        except Exception as ex:
            return self._json({"error": "%s: %s" % (type(ex).__name__, ex)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8") or "{}")
        except Exception as ex:
            return self._json({"error": "invalid JSON: %s" % ex}, 400)
        try:
            if p == "/api/heatmap":
                return self._json(heatmap_engine(req))
            if p == "/api/diagnose":
                return self._json(diagnose_engine(req))
            if p == "/api/wlc/analyze":
                if not str(req.get("text") or "").strip():
                    return self._json({"error": "请粘贴日志、配置或抓包文本后再分析"}, 400)
                return self._json(wlc_analyze(req.get("text"), str(req.get("vendor") or "")))
            if p == "/api/air/import":
                out = air_import(req.get("names") or [])
                return self._json(out)
            if p == "/api/survey/session":
                store, SV = _survey()
                return self._json({"ok": True, "session": store.set_session(req.get("name")),
                                   "state": survey_state()})
            if p == "/api/survey/profile":
                store, SV = _survey()
                key = str(req.get("key") or "data")
                store.set_profile(key, req.get("custom"))
                return self._json({"ok": True, "state": survey_state()})
            if p == "/api/survey/import-csv":
                out = survey_import_csv(req)
                return self._json(out, 200 if out.get("ok") else 400)
            if p == "/api/local/wlanreport":
                DK, _hist = _desktop()
                return self._json(DK.generate_wlan_report())
            if p == "/api/history/clear":
                _DK, hist = _desktop()
                return self._json(hist.clear())
            if p == "/api/backup/import":
                DK, _hist = _desktop()
                s = str(req.get("data") or "")
                if "," in s[:64] and s.strip().startswith("data:"):
                    s = s.split(",", 1)[1]
                try:
                    raw = base64.b64decode(s)
                except Exception as ex:
                    return self._json({"ok": False, "error": "备份内容解码失败：" + str(ex)})
                if not raw or len(raw) > 60 * 1024 * 1024:
                    return self._json({"ok": False, "error": "备份文件为空或超过 60 MB"})
                out = DK.restore_backup(USER_DIR, raw)
                if out.get("ok"):
                    _reset_in_memory()
                return self._json(out)
            if p == "/api/survey/snapshot":
                return self._json(survey_snapshot(req))
            if p == "/api/extras/state":
                return self._json(extras_set(req))
            if p == "/api/survey/point":
                out = survey_add_point(req)
                return self._json(out, 400 if not out.get("ok") else 200)
            if p == "/api/survey/delete":
                store, SV = _survey()
                return self._json({"ok": True, "removed": store.delete(req.get("id")),
                                   "state": survey_state()})
            if p == "/api/survey/clear":
                store, SV = _survey()
                return self._json({"ok": True,
                                   "removed": store.clear(session_only=not bool(req.get("all"))),
                                   "state": survey_state()})
            if p == "/api/survey/calibrate":
                return self._json(survey_calibrate(req))
            if p == "/api/survey/apply":
                cal = SURVEY.get("calibration") or {}
                if not cal.get("ok"):
                    return self._json({"ok": False, "error": "请先完成模型校准"}, 400)
                cal = dict(cal, applied=True)
                SURVEY["calibration"] = cal
                return self._json({"ok": True, "calibration": cal})
            if p == "/api/audit/refresh":
                return self._json(audit_state(refresh=True))
            if p == "/api/roam/start":
                return self._json(roam_start(req))
            if p == "/api/roam/stop":
                return self._json(_roam_rec().stop())
            if p == "/api/roam/reset":
                rec = _roam_rec()
                rec.stop()
                rec.reset(str(req.get("point") or ""))
                return self._json(rec.snapshot())
            if p == "/api/roam/scan":
                return self._json(roam_force_scan())
            if p == "/api/wlan/use":
                out = wlan_use_ssid(req)
                if out.get("error"):
                    return self._json(out, 400)
                return self._json(out)
            if p == "/api/wlan/auto":
                SSID_STATE["auto_local"] = bool(req.get("enabled", True))
                save_ssids()
                return self._json(ssid_public())
            if p == "/api/ssid/select":
                sid = str(req.get("id", ""))
                if not ssid_get(sid):
                    return self._json({"error": "SSID 不存在"}, 404)
                SSID_STATE["active_id"] = sid
                save_ssids()
                return self._json(ssid_public())
            if p == "/api/ssid/save":
                item = dict(req.get("ssid") or {})
                name = str(item.get("name", "")).strip()
                if not name:
                    return self._json({"error": "SSID 名称不能为空"}, 400)
                if any(s.get("name") == name and str(s.get("id")) != str(item.get("id", ""))
                       for s in SSID_STATE["list"]):
                    return self._json({"error": "SSID 名称重复"}, 400)
                sid = str(item.get("id") or ("ssid-%d" % int(time.time() * 1000)))
                item.update(id=sid, name=name,
                            band=item.get("band") if item.get("band") in ("双频", "2.4", "5", "6") else "双频",
                            vlan=int(item.get("vlan") or 0),
                            min_rssi=float(item.get("min_rssi") or -70),
                            max_clients=int(item.get("max_clients") or 60),
                            ap_ids=[int(x) for x in (item.get("ap_ids") or []) if str(x).isdigit()],
                            broadcast=bool(item.get("broadcast", True)),
                            enabled=bool(item.get("enabled", True)))
                idx = next((i for i, s in enumerate(SSID_STATE["list"]) if str(s.get("id")) == sid), -1)
                if idx >= 0:
                    SSID_STATE["list"][idx] = item
                else:
                    SSID_STATE["list"].append(item)
                    SSID_STATE["active_id"] = sid
                save_ssids()
                return self._json(ssid_public())
            if p == "/api/ssid/delete":
                sid = str(req.get("id", ""))
                if len(SSID_STATE["list"]) <= 1:
                    return self._json({"error": "至少保留一个 SSID"}, 400)
                SSID_STATE["list"] = [s for s in SSID_STATE["list"] if str(s.get("id")) != sid]
                if SSID_STATE["active_id"] == sid:
                    SSID_STATE["active_id"] = SSID_STATE["list"][0]["id"]
                save_ssids()
                return self._json(ssid_public())
            if p == "/api/ssid/reset":
                SSID_STATE.update(active_id=DEFAULT_SSIDS[0]["id"], list=_ssid_defaults())
                save_ssids()
                return self._json(ssid_public())
            if p == "/api/live/config":
                return self._json(live_configure(req.get("aps"), req.get("ppm", 10), req.get("n_clients", 36)))
            if p == "/api/live/pause":
                LIVE["paused"] = bool(req.get("paused", False))
                LIVE["tick"] = time.time()
                return self._json({"ok": True, "paused": LIVE["paused"]})
            if p == "/api/live/reset":
                return self._json(live_configure(req.get("aps"), req.get("ppm", 10), req.get("n_clients", 36)))
            if p == "/api/interference":
                return self._json(interference_analysis(req.get("aps"), req.get("ppm", 10),
                                                        req.get("band", "2.4"), req.get("ssid_id")))
            if p == "/api/interference/add":
                kind = str(req.get("kind", ""))
                tpl = next((t for t in INTERFERER_LIBRARY if t[0] == kind), INTERFERER_LIBRARY[0])
                ka, b, center, width, power, duty, advice = tpl
                nid = max([i["id"] for i in LIVE["interferers"]] or [0]) + 1
                item = dict(id=nid, kind=ka, band=b, center=center, width=width, power=power,
                            duty=duty, x=float(req.get("x", 500)), y=float(req.get("y", 350)),
                            radius_m=18, advice=advice)
                LIVE["interferers"].append(item)
                _event("warn", "interference", "现场新增干扰源：%s @ (%.0f, %.0f)" % (ka, item["x"], item["y"]))
                return self._json({"ok": True, "interferers": LIVE["interferers"]})
            if p == "/api/interference/remove":
                iid = req.get("id")
                before = len(LIVE["interferers"])
                LIVE["interferers"] = [i for i in LIVE["interferers"] if str(i["id"]) != str(iid)]
                return self._json({"ok": True, "removed": before - len(LIVE["interferers"]),
                                   "interferers": LIVE["interferers"]})
            if p == "/api/floorplan":
                data = str(req.get("data", ""))
                if "," in data:
                    data = data.split(",", 1)[1]
                blob = base64.b64decode(data)
                if len(blob) > 20 * 1024 * 1024:
                    return self._json({"error": "图片过大（上限 20 MB）"}, 400)
                with open(os.path.join(USER_DIR, "floorplan.png"), "wb") as f:
                    f.write(blob)
                return self._json({"ok": True, "url": "/api/floorplan?t=%d" % (os.path.getmtime(os.path.join(USER_DIR, "floorplan.png")) * 1000)})
            if p == "/api/project/save":
                name = self._safe_name(req.get("name") or "未命名项目")
                if not name:
                    return self._json({"error": "项目名无效"}, 400)
                with open(os.path.join(PROJECT_DIR, name + ".json"), "w", encoding="utf-8") as f:
                    json.dump(req.get("payload", {}), f, ensure_ascii=False)
                return self._json({"ok": True, "name": name})
            if p == "/api/project/delete":
                name = self._safe_name(req.get("name") or "")
                path = os.path.join(PROJECT_DIR, name + ".json")
                if os.path.isfile(path):
                    os.remove(path)
                return self._json({"ok": True})
            return self._json({"error": "unknown endpoint"}, 404)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            return self._json({"error": "%s: %s" % (type(ex).__name__, ex)}, 500)


def find_port(start, tries=30):
    for i in range(tries):
        p = start + i
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    return None


def main():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    ensure_dirs()
    port = find_port(args.port)
    if port is None:
        log("[ERROR] no free port near %d" % args.port)
        return 1
    url = "http://127.0.0.1:%d/" % port
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    log("=" * 62)
    log("  %s  v%s" % (APP_NAME, VERSION))
    log("  无线故障排除 / 图纸热力图 / 无线优化 · 单机离线版")
    log("=" * 62)
    log("  本机地址 : " + url)
    log("  型号库   : %d 条 / %d 家厂商" % (len(CATALOG.get("records", [])), CATALOG.get("stats", {}).get("vendors", 0)))
    log("  用户数据 : " + USER_DIR)
    log("  关闭方式 : 在本窗口按 Ctrl+C，或直接关闭本窗口")
    log("=" * 62)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("\n正在退出 ...")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
