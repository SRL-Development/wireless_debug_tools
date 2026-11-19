# -*- coding: utf-8 -*-
"""
extras.py — v0.12 增补能力（纯标准库 / 全程离线 / 只读本机）

  1) 报告模板与品牌信息：标题 / 机构 / 项目 / 人员 / Logo / 页脚 / 保密标记 / 签署栏
  2) 多楼层（会话）项目管理：楼层名、实际宽度、备注，供报告合订
  3) 优化前后基线快照与差异对比：指标 delta、问题增减、逐点变化
  4) 点位 CSV 批量导入：中文 / 英文表头兼容，允许导入历史实测数据
  5) 审计规则自定义：逐条启停 + 严重度覆盖，重算健康分

数据一律落在 %APPDATA%/WirelessNetOptimizer/*.json，写入采用临时文件 + 原子替换。
不联网、不发包、不连接现网设备，也不改变原有「只读」定位。
"""
import csv
import hashlib
import io
import json
import os
import time

# ---------------------------------------------------------------- 规则目录
# 与 survey.audit() 中 _finding() 使用的 ID 一一对应；顺序即界面显示顺序。
RULE_CATALOG = [
    {"id": "COV-01", "group": "覆盖验收", "title": "勘测点不达标", "severity": "high"},
    {"id": "COV-02", "group": "覆盖验收", "title": "勘测点数量不足 / 置信度低", "severity": "medium"},
    {"id": "CH-01", "group": "信道规划", "title": "2.4G 非 1/6/11 信道", "severity": "high"},
    {"id": "CH-02", "group": "信道规划", "title": "2.4G 射频过密", "severity": "medium"},
    {"id": "CH-03", "group": "信道规划", "title": "最差信道干扰指数过高", "severity": "high"},
    {"id": "CH-04", "group": "信道规划", "title": "40/80 MHz 宽信道占用", "severity": "medium"},
    {"id": "CH-05", "group": "信道规划", "title": "5G DFS 信道占用", "severity": "medium"},
    {"id": "CAP-01", "group": "容量", "title": "信道利用率过高", "severity": "high"},
    {"id": "CFG-01", "group": "配置", "title": "SSID 配置异常 / 重叠", "severity": "medium"},
    {"id": "SEC-01", "group": "安全", "title": "开放网络", "severity": "high"},
    {"id": "SEC-02", "group": "安全", "title": "弱加密（WEP / TKIP / WPA1）", "severity": "high"},
    {"id": "SEC-03", "group": "安全", "title": "疑似仿冒 SSID / Evil Twin", "severity": "critical"},
    {"id": "SEC-04", "group": "安全", "title": "未登记 SSID（需人工核查）", "severity": "low"},
    {"id": "SEC-05", "group": "安全", "title": "隐藏 BSS", "severity": "low"},
    {"id": "ROAM-01", "group": "漫游", "title": "漫游事件 / 掉线异常", "severity": "high"},
    {"id": "ROAM-02", "group": "漫游", "title": "粘滞终端 / 弱信号关联", "severity": "medium"},
    {"id": "CAP-02", "group": "协议健壮性", "title": "管理帧保护 PMF 缺失", "severity": "medium"},
    {"id": "CAP-03", "group": "漫游能力", "title": "缺少 802.11r/k/v 快速漫游", "severity": "medium"},
    {"id": "SEC-06", "group": "安全", "title": "开放网络未启用 OWE", "severity": "low"},
    {"id": "SEC-07", "group": "安全", "title": "WPA2/WPA3 过渡模式", "severity": "info"},
]
SEVERITIES = ["critical", "high", "medium", "low", "info"]
SEV_TEXT = {"critical": "严重", "high": "高", "medium": "中", "low": "低", "info": "提示"}

# ---------------------------------------------------------------- CSV 字段
CSV_MAP = {
    "点位": "label", "label": "label", "name": "label", "point": "label",
    "坐标x": "x", "x": "x",
    "坐标y": "y", "y": "y",
    "会话": "session", "session": "session", "楼层": "session", "floor": "session",
    "ssid": "ssid", "bssid": "bssid",
    "rssi": "rssi", "rssi(dbm)": "rssi", "信号强度": "rssi",
    "snr": "snr", "snr(db)": "snr", "信噪比": "snr",
    "sinr": "sinr", "sinr(db)": "sinr", "信干噪比": "sinr",
    "频段": "band", "band": "band",
    "信道": "channel", "channel": "channel", "ch": "channel",
    "带宽(mhz)": "width_mhz", "width_mhz": "width_mhz", "带宽": "width_mhz",
    "加密": "security", "security": "security",
    "干扰指数": "interference_index", "interference_index": "interference_index", "index": "interference_index",
    "可见射频": "visible_radios", "visible_radios": "visible_radios", "radios": "visible_radios",
    "利用率(%)": "utilization_pct", "utilization_pct": "utilization_pct", "利用率": "utilization_pct",
    "空口bss数": "bss_total", "bss_total": "bss_total",
    "说明": "note", "note": "note", "备注": "note",
    "采集时间": "at_text", "at_text": "at_text",
}
CSV_NUM_INT = {"channel", "width_mhz", "visible_radios", "bss_total"}
CSV_NUM_FLOAT = {"x", "y", "rssi", "snr", "sinr", "interference_index", "utilization_pct"}

DEFAULTS = {
    "report": {
        "title": "无线现场勘测与验收报告", "org": "", "project": "",
        "engineer": "", "reviewer": "", "logo": "", "footer": "",
        "show_sign": True, "confidential": True, "show_boundary": True,
    },
    "floors": {"current": "", "items": []},
    "rules": {"disabled": [], "severity": {}},
    "snapshots": {},
}
FILES = {
    "report": "report_options.json", "floors": "floors.json",
    "rules": "audit_rules.json", "snapshots": "survey_snapshots.json",
}


def _now(t=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t or time.time()))


class Extras:
    def __init__(self, user_dir):
        self.user_dir = user_dir
        self.cache = {}
        for k in DEFAULTS:
            self.cache[k] = self._read(k)

    def _path(self, key):
        return os.path.join(self.user_dir, FILES[key])

    def _read(self, key):
        try:
            with open(self._path(key), "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                base = dict(DEFAULTS[key])
                if key == "snapshots":
                    return {str(k2): v for k2, v in d.items() if isinstance(v, dict)}
                base.update(d)
                return base
        except Exception:
            pass
        return dict(DEFAULTS[key]) if key != "snapshots" else {}

    def get(self, key):
        return self.cache.setdefault(key, self._read(key))

    def save(self, key, data=None):
        if data is not None:
            self.cache[key] = data
        try:
            os.makedirs(self.user_dir, exist_ok=True)
            tmp = self._path(key) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cache[key], f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path(key))
            return True
        except Exception:
            return False

    # ---------------- 报告模板 ----------------
    def set_report(self, patch):
        cur = dict(self.get("report"))
        for k in ("title", "org", "project", "engineer", "reviewer", "footer"):
            if k in patch:
                cur[k] = str(patch[k] or "")[:120]
        for k in ("show_sign", "confidential", "show_boundary"):
            if k in patch:
                cur[k] = bool(patch[k])
        if "logo" in patch:
            logo = str(patch["logo"] or "")
            # 只接受内联图片，限制 320 KB，避免大文件拖慢报告与项目文件
            if logo and not (logo.startswith("data:image/") and len(logo) <= 440000):
                return {"ok": False, "error": "Logo 只支持内联 PNG/JPG，且不超过约 320 KB"}
            cur["logo"] = logo
        self.save("report", cur)
        return {"ok": True, "report": self.public_report()}

    def public_report(self):
        r = dict(self.get("report"))
        r["has_logo"] = bool(r.get("logo"))
        r.pop("logo", None)          # 前端表单不需要回传整张图
        return r

    # ---------------- 多楼层 / 项目 ----------------
    def floor_add(self, name, width_m=None, note=""):
        name = str(name or "").strip()[:60]
        if not name:
            return {"ok": False, "error": "楼层 / 会话名称不能为空"}
        f = self.get("floors")
        items = [x for x in f.get("items", []) if str(x.get("name")) != name]
        try:
            w = float(width_m) if width_m not in (None, "") else None
        except (TypeError, ValueError):
            w = None
        items.append({"name": name, "width_m": w, "note": str(note or "")[:120],
                      "created": _now()})
        f = {"current": name, "items": items[-40:]}
        self.save("floors", f)
        return {"ok": True, "floors": f, "current": name}

    def floor_set_current(self, name):
        f = dict(self.get("floors"))
        f["current"] = str(name or "")[:60]
        self.save("floors", f)
        return {"ok": True, "floors": f}

    # ---------------- 审计规则 ----------------
    def set_rules(self, patch):
        cur = self.get("rules")
        if "disabled" in patch:
            valid = set(x["id"] for x in RULE_CATALOG)
            cur["disabled"] = [str(x) for x in (patch["disabled"] or []) if str(x) in valid]
        if "severity" in patch:
            cur["severity"] = {str(k): v for k, v in (patch["severity"] or {}).items()
                               if v in SEVERITIES and str(k) in set(x["id"] for x in RULE_CATALOG)}
        if "reset" in patch and patch["reset"]:
            cur = {"disabled": [], "severity": {}}
        self.save("rules", cur)
        return {"ok": True, "rules": cur}

    def state(self):
        return {
            "ok": True, "report": self.public_report(), "floors": self.get("floors"),
            "rules": self.get("rules"), "catalog": RULE_CATALOG,
            "severities": [{"key": k, "name": SEV_TEXT[k]} for k in SEVERITIES],
            "snapshots": self.snapshot_list(),
        }

    # ---------------- 基线快照 / 优化前后 diff ----------------
    def snapshot_list(self):
        out = []
        for k, v in sorted(self.get("snapshots").items(), key=lambda kv: -(kv[1].get("at") or 0)):
            out.append({"name": k, "at_text": v.get("at_text"), "session": v.get("session"),
                        "total": v.get("total"), "pass_rate": v.get("pass_rate"),
                        "health": v.get("health")})
        return out

    def snapshot_save(self, name, session, ev, au, points):
        name = str(name or "").strip()[:60] or ("基线-" + time.strftime("%m%d-%H%M%S"))
        au = au or {}
        snap = {
            "name": name, "at": time.time(), "at_text": _now(),
            "session": session or "", "profile": (ev.get("profile") or {}).get("name"),
            "total": ev.get("total", 0), "pass": ev.get("pass", 0),
            "edge": ev.get("edge", 0), "fail": ev.get("fail", 0),
            "pass_rate": ev.get("pass_rate"), "stats": ev.get("stats") or {},
            "health": au.get("health"), "grade": au.get("grade"),
            "findings": [{"id": f.get("id"), "severity": f.get("severity"),
                          "title": f.get("title")} for f in (au.get("findings") or [])],
            "points": [{"label": p.get("label"), "x": p.get("x"), "y": p.get("y"),
                        "rssi": p.get("rssi"), "sinr": p.get("sinr"),
                        "interference_index": p.get("interference_index")}
                       for p in (points or [])],
        }
        snaps = dict(self.get("snapshots"))
        snaps[name] = snap
        self.save("snapshots", snaps)
        return {"ok": True, "name": name, "snapshot": snap, "list": self.snapshot_list()}

    def snapshot_delete(self, name):
        snaps = dict(self.get("snapshots"))
        if name not in snaps:
            return {"ok": False, "error": "快照不存在"}
        del snaps[name]
        self.save("snapshots", snaps)
        return {"ok": True, "list": self.snapshot_list()}

    def snapshot_diff(self, before, after):
        snaps = self.get("snapshots")
        if before not in snaps or after not in snaps:
            return {"ok": False, "error": "请选择两个已保存的基线快照"}
        a, b = snaps[before], snaps[after]
        # (label, key path, 单位, 越大越好?)
        metric_defs = [
            ("勘测点位数", "total", "", True), ("达标率", "pass_rate", "%", True),
            ("审计健康分", "health", "", True),
            ("最弱 RSSI", "stats.rssi_min", "dBm", True),
            ("平均 RSSI", "stats.rssi_avg", "dBm", True),
            ("最低 SINR", "stats.sinr_min", "dB", True),
            ("平均 SINR", "stats.sinr_avg", "dB", True),
            ("平均干扰指数", "stats.index_avg", "", False),
            ("最高干扰指数", "stats.index_max", "", False),
        ]

        def pick(d, path):
            cur = d
            for k in path.split("."):
                cur = (cur or {}).get(k) if isinstance(cur, dict) else None
            return cur if isinstance(cur, (int, float)) else None

        metrics = []
        for label, key, unit, higher_better in metric_defs:
            va, vb = pick(a, key), pick(b, key)
            delta = round(vb - va, 1) if va is not None and vb is not None else None
            metrics.append({"label": label, "unit": unit, "before": va, "after": vb,
                            "delta": delta,
                            "better": (None if delta is None else (delta > 0 if higher_better else delta < 0)),
                            "flat": (delta == 0 if delta is not None else True)})
        fa = {f["id"]: f for f in a.get("findings", [])}
        fb = {f["id"]: f for f in b.get("findings", [])}
        added = [fb[k] for k in fb if k not in fa]
        removed = [fa[k] for k in fa if k not in fb]
        changed = [{"id": k, "before": fa[k]["severity"], "after": fb[k]["severity"],
                    "title": fb[k].get("title")}
                   for k in fa if k in fb and fa[k]["severity"] != fb[k]["severity"]]
        pa = {p.get("label"): p for p in a.get("points", [])}
        pb = {p.get("label"): p for p in b.get("points", [])}
        point_rows = []
        for lab in sorted(set(pa) | set(pb), key=str):
            x, y = pa.get(lab), pb.get(lab)
            if not x:
                point_rows.append({"label": lab, "state": "新增", "delta_rssi": None, "delta_sinr": None})
                continue
            if not y:
                point_rows.append({"label": lab, "state": "删除", "delta_rssi": None, "delta_sinr": None})
                continue
            dr = (round(y["rssi"] - x["rssi"], 1) if isinstance(x.get("rssi"), (int, float))
                  and isinstance(y.get("rssi"), (int, float)) else None)
            ds = (round(y["sinr"] - x["sinr"], 1) if isinstance(x.get("sinr"), (int, float))
                  and isinstance(y.get("sinr"), (int, float)) else None)
            point_rows.append({"label": lab, "state": "复测", "before_rssi": x.get("rssi"),
                               "after_rssi": y.get("rssi"), "delta_rssi": dr, "delta_sinr": ds})
        return {"ok": True, "before": {"name": before, "at_text": a.get("at_text"), "session": a.get("session")},
                "after": {"name": after, "at_text": b.get("at_text"), "session": b.get("session")},
                "metrics": metrics, "added": added, "removed": removed, "changed": changed,
                "point_rows": point_rows,
                "note": "差异按两次快照的固化数据计算；不同路线 / 不同时段的点位只有同名才做逐点配对，未配对的点标记为新增或删除。"}


# ---------------------------------------------------------------- CSV 导入
def parse_points_csv(text):
    """把导出的点位 CSV（或同结构的现场记录）解析成点位字典列表。"""
    if not text or not text.strip():
        return [], ["文件为空"], 0
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        rows = list(reader)
    except Exception as ex:
        return [], ["CSV 解析失败：%s" % ex], 0
    if not rows:
        return [], ["没有数据行（至少需要表头 + 1 行点位）"], 0
    heads = [(h or "").strip() for h in (reader.fieldnames or [])]
    mapping = {}
    for i, h in enumerate(heads):
        key = CSV_MAP.get(h.strip().lower())
        if key:
            mapping[(reader.fieldnames or [])[i]] = key
    if "x" not in mapping.values() or "y" not in mapping.values():
        return [], ["缺少必需列「坐标X / 坐标Y」（可用「导出点位 CSV」作为模板）"], len(rows)
    out, errors, skipped = [], [], 0
    for n, row in enumerate(rows, 2):
        p = {}
        for col, key in mapping.items():
            raw = (row.get(col) or "").strip()
            if raw == "":
                continue
            if key in CSV_NUM_INT or key in CSV_NUM_FLOAT:
                try:
                    v = float(raw)
                    p[key] = int(v) if key in CSV_NUM_INT else round(v, 1)
                except ValueError:
                    errors.append("第 %d 行 %s 不是数字：%s" % (n, col, raw[:20]))
                    continue
            else:
                p[key] = raw[:200]
        if p.get("x") is None or p.get("y") is None:
            skipped += 1
            continue
        p["x"] = max(0.0, min(1000.0, float(p["x"])))
        p["y"] = max(0.0, min(700.0, float(p["y"])))
        p.setdefault("label", "P%02d" % (len(out) + 1))
        p["source"] = "csv-import"
        p["has_data"] = p.get("rssi") is not None
        out.append(p)
    return out, errors[:20], skipped


CSV_TEMPLATE_HEADERS = ["点位", "会话", "坐标X", "坐标Y", "采集时间", "SSID", "BSSID",
                        "RSSI(dBm)", "SNR(dB)", "SINR(dB)", "频段", "信道", "带宽(MHz)",
                        "加密", "干扰指数", "可见射频", "利用率(%)", "空口BSS数", "说明"]


def csv_template():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_TEMPLATE_HEADERS)
    w.writerow(["P01", "3F 东翼", 320, 196, _now(), "Corp-WiFi", "", -53, 39, 27,
                "5", 36, 80, "WPA2-Enterprise", 22, 7, 18, 12, "示例行，导入前请删除"])
    return "\ufeff" + buf.getvalue()


# ---------------------------------------------------------------- 规则应用
def apply_rules(au, cfg, recompute):
    """按用户配置过滤 / 改严重度，再用 survey.recompute_audit 重算健康分。"""
    if not isinstance(au, dict) or not au.get("ok"):
        return au
    disabled = set((cfg or {}).get("disabled") or [])
    override = (cfg or {}).get("severity") or {}
    kept = []
    for f in au.get("findings") or []:
        if f.get("id") in disabled:
            continue
        sev = override.get(f.get("id"))
        if sev in SEV_TEXT and sev != f.get("severity"):
            f = dict(f, severity=sev, severity_text=SEV_TEXT[sev])
        kept.append(f)
    return recompute(kept, au.get("rollup") or {})


def digest(text):
    return hashlib.md5(str(text).encode("utf-8", "replace")).hexdigest()[:10]
