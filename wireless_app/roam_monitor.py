# -*- coding: utf-8 -*-
"""移动漫游监测（只读）。

采集两层，互相不干扰：
  1) 高频：netsh wlan show interfaces（默认 1 秒）→ 当前关联的 SSID / BSSID / 信道 / 信号 / 速率
  2) 低频：WlanGetNetworkBssList（默认 20 秒，读系统扫描缓存，不触发 WlanScan）→ 周边 BSS 与信道拥挤

判定：
  BSSID 变化（同 SSID）      → 二层漫游，记录 from → to、驻留时长、信号变化
  SSID 变化                  → 跨 SSID 切换
  频段变化                   → 跨频段漫游（2.4G ↔ 5G）
  连接消失 / 恢复            → 掉线 / 重连，记录空窗时长

口径说明（报告里必须保留）：
  · netsh 不提供 dBm，只有信号百分比，RSSI 为百分比换算值，分辨率约 2 dB，不是实测 dBm
  · 切换耗时受轮询间隔限制，只能给出「≤ 轮询间隔」的观测值，不是空口抓包级时延
  · 周边 BSS 的 RSSI 来自系统扫描缓存，可能滞后数十秒；需要精确值时用「强制扫描」按钮
全程只读：不连接、不断开、不修改任何无线配置、不联网、不上传。
"""
import csv
import io
import threading
import time

MAX_EVENTS = 500
MAX_SAMPLES = 1200

KIND_LABEL = {
    "roam": "同 SSID 漫游",
    "band-roam": "跨频段漫游",
    "ssid-switch": "跨 SSID 切换",
    "connect": "建立关联",
    "disconnect": "掉线",
}


def _rssi(conn):
    """优先 dBm；只有百分比时按 Windows 线性口径换算，并标明来源。"""
    if not conn:
        return None, "无"
    v = conn.get("rssi_dbm")
    if isinstance(v, int):
        return v, "netsh dBm"
    p = conn.get("signal_pct")
    if isinstance(p, int):
        return int(round(p / 2.0 - 100)), "百分比换算"
    return None, "无"


def _compact(conn):
    if not conn:
        return None
    rssi, src = _rssi(conn)
    return {
        "ssid": conn.get("ssid") or "",
        "bssid": (conn.get("bssid") or "").lower(),
        "channel": conn.get("channel"),
        "band": conn.get("band") or "",
        "rssi": rssi,
        "rssi_source": src,
        "signal_pct": conn.get("signal_pct"),
        "radio": conn.get("radio") or "",
        "interface": conn.get("name") or "",
        "profile": conn.get("profile") or "",
        "rx_rate": conn.get("rx_rate") or "",
        "tx_rate": conn.get("tx_rate") or "",
        "auth": conn.get("auth") or "",
        "cipher": conn.get("cipher") or "",
    }


def _hm(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


class Recorder(object):
    def __init__(self, state_fn, bss_fn, analyze_fn):
        self.state_fn = state_fn       # () -> {"connected": {...}|None, "interfaces": [...], "error": ""}
        self.bss_fn = bss_fn           # (trigger: bool) -> {"ok":..., "bss": [...], "scanned_at": ...}
        self.analyze_fn = analyze_fn   # (bss, point) -> air_quality.analyze(...)
        self.lock = threading.RLock()
        self.stop_evt = threading.Event()
        self.thread = None
        self.reset("")

    # ---------------- 生命周期 ----------------
    def reset(self, point=""):
        with self.lock:
            self.point = point or "移动采集"
            self.started_at = None
            self.events = []
            self.samples = []
            self.cur = None
            self.since = None
            self.last_seen = None
            self.last_sample_t = None
            self.disconnect_at = None
            self.air = None
            self.air_error = ""
            self.scans = []
            self.last_active_scan = 0.0
            self.scan_win = None
            self.error = ""
            self.poll = 1.0
            self.air_every = 20.0
            self.polls = 0
            self.first_state = None

    def start(self, point="", poll=1.0, air_every=20.0, refresh_air=True):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.point = point or self.point
                self.poll = max(0.5, min(5.0, float(poll or 1.0)))
                self.air_every = max(8.0, min(300.0, float(air_every or 20.0)))
                return {"ok": True, "already": True, "state": self.snapshot()}
            self.reset(point)
            self.poll = max(0.5, min(5.0, float(poll or 1.0)))
            self.air_every = max(8.0, min(300.0, float(air_every or 20.0)))
            self.started_at = time.time()
            self.stop_evt = threading.Event()
            self.thread = threading.Thread(target=self._loop, name="roam-monitor", daemon=True)
            self.thread.start()
        if refresh_air:
            threading.Thread(target=self._refresh_air, kwargs={"trigger": False},
                             daemon=True).start()
        return {"ok": True, "state": self.snapshot()}

    def stop(self):
        self.stop_evt.set()
        t = self.thread
        if t and t.is_alive():
            t.join(timeout=2.5)
        with self.lock:
            snap = self.snapshot()
            snap["stopped"] = True
            return snap

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    # ---------------- 采集循环 ----------------
    def _loop(self):
        next_air = time.time() + self.air_every
        while not self.stop_evt.is_set():
            t0 = time.time()
            try:
                st = self.state_fn()
            except Exception as e:            # 单个轮询失败不终止整体
                st = {"connected": None, "error": "%s: %s" % (type(e).__name__, e)}
            try:
                self._ingest(st, t0)
            except Exception as e:
                with self.lock:
                    self.error = "状态解析失败：%s" % e
            if time.time() >= next_air:
                next_air = time.time() + self.air_every
                threading.Thread(target=self._refresh_air, kwargs={"trigger": False},
                                 daemon=True).start()
            left = self.poll - (time.time() - t0)
            self.stop_evt.wait(max(0.15, left))

    def _ingest(self, st, now):
        conn = _compact(st.get("connected"))
        err = st.get("error") or ""
        with self.lock:
            self.polls += 1
            if self.first_state is None:
                self.first_state = conn
            if not err:
                self.error = ""
            elif not conn:
                self.error = err
            prev = self.cur
            prev_t = self.last_sample_t
            # 信号采样（掉线也采，用于画断点）
            rssi = conn.get("rssi") if conn else None
            self.samples.append({"t": round(now, 1), "rssi": rssi,
                                 "bssid": (conn or {}).get("bssid", ""),
                                 "ssid": (conn or {}).get("ssid", ""),
                                 "ch": (conn or {}).get("channel")})
            if len(self.samples) > MAX_SAMPLES:
                del self.samples[:len(self.samples) - MAX_SAMPLES]
            self.last_sample_t = now
            if conn and conn.get("bssid"):
                self.last_seen = now
            if not conn and not prev:
                return
            if conn and prev and conn.get("bssid") == prev.get("bssid"):
                return          # 同一 AP，只更新采样
            self._event(prev, conn, now, prev_t)

    def _event(self, prev, cur, now, prev_t=None):
        kind = None
        if prev and not cur:
            kind = "disconnect"
        elif prev and cur:
            if prev.get("ssid") != cur.get("ssid"):
                kind = "ssid-switch"
            elif prev.get("band") != cur.get("band"):
                kind = "band-roam"
            else:
                kind = "roam"
        elif cur and not prev:
            kind = "connect"
        if not kind:
            return
        dwell = round(now - self.since, 1) if self.since else None
        if kind == "disconnect":
            gap = None
            self.disconnect_at = now
        elif kind == "connect":
            gap = round(now - self.disconnect_at, 1) if self.disconnect_at else None
            dwell = None
        else:
            gap = round(now - prev_t, 1) if prev_t else None
            self.disconnect_at = None
        ev = {
            "t": round(now, 1), "ts": _hm(now), "kind": kind,
            "label": KIND_LABEL.get(kind, kind),
            "from": prev, "to": cur,
            "dwell_sec": dwell, "gap_sec": gap,
            "rssi_delta": (cur.get("rssi") - prev.get("rssi"))
                          if (cur and prev and cur.get("rssi") is not None
                              and prev.get("rssi") is not None) else None,
            "note": self._note(kind, prev, cur, gap),
            "scan_window": bool(self.scan_win and self.scan_win[0] <= now <= self.scan_win[1]),
        }
        if ev["scan_window"]:
            ev["note"] += "（落在主动扫描窗口内，可能受网卡切信道影响）"
        self.events.append(ev)
        if len(self.events) > MAX_EVENTS:
            del self.events[:len(self.events) - MAX_EVENTS]
        self.cur = cur
        self.since = now

    @staticmethod
    def _note(kind, prev, cur, gap):
        if kind == "disconnect":
            return "关联丢失：可能走出覆盖、切换空窗或网卡省电。空窗超过 2 秒才算真掉线。"
        if kind == "connect":
            return ("掉线后重新关联，空窗 %s 秒" % gap) if gap is not None else "开始采集时的关联状态"
        a, b = prev or {}, cur or {}
        band = ""
        if a.get("band") != b.get("band") and a.get("band") and b.get("band"):
            band = " · %sG → %sG" % (a.get("band"), b.get("band"))
        ch = ""
        if a.get("channel") != b.get("channel") and b.get("channel"):
            ch = " · ch%s → ch%s" % (a.get("channel"), b.get("channel"))
        d = ""
        if a.get("rssi") is not None and b.get("rssi") is not None:
            delta = b["rssi"] - a["rssi"]
            d = " · 信号 %+d dB" % delta
        g = " · 空窗 %s 秒（≤ 轮询间隔，非空口时延）" % gap if gap else ""
        return KIND_LABEL.get(kind, kind) + band + ch + d + g

    # ---------------- 周边空口 ----------------
    def _refresh_air(self, trigger=False):
        """trigger=True 强制主动扫描；否则先读缓存，缓存太少（<6 个 BSS）时自动补一次主动扫描。

        主动扫描会让网卡短暂切信道，可能造成 1-2 秒瞬断，所以：① 最多 15 秒补一次；
        ② 扫描窗口记进 self.scans，落在窗口内的事件会打标记，避免把扫描伪影当成真掉线。
        """
        mode = "cache"
        t0 = time.time()
        try:
            raw = self.bss_fn(False)
        except Exception as e:
            with self.lock:
                self.air_error = "%s: %s" % (type(e).__name__, e)
            return
        fresh = bool(self.air and (t0 - self.air["at"]) < 3.0)
        if fresh and not trigger:
            return
        need = bool(trigger) or (raw.get("ok") and len(raw.get("bss") or []) < 6
                                 and (t0 - self.last_active_scan) >= 15)
        if need and (t0 - self.last_active_scan) >= 8:
            mode = "active"
            self.last_active_scan = t0
            self.scan_win = (t0, t0 + 7.0)
            try:
                raw = self.bss_fn(True)
            except Exception as e:
                with self.lock:
                    self.air_error = "%s: %s" % (type(e).__name__, e)
                return
        if not raw.get("ok"):
            with self.lock:
                self.air_error = raw.get("error") or "扫描失败"
            return
        bss = raw.get("bss") or []
        point = self.point or "移动采集"
        try:
            ana = self.analyze_fn(bss, point)
        except Exception as e:
            with self.lock:
                self.air_error = "干扰分析失败：%s" % e
            return
        with self.lock:
            self.air = {
                "at": time.time(), "scanned_at": raw.get("scanned_at") or _hm(time.time()),
                "trigger": mode == "active", "mode": mode,
                "bss": bss,
                "channels": ana.get("channels") or [],
                "radios": ana.get("radios") or [],
                "rollup": ana.get("rollup") or {},
                "issues": ana.get("issues") or [],
                "recommend": ana.get("recommend") or {},
                "ssids": ana.get("ssids") or [],
            }
            self.scans.append({"t": round(t0, 1), "ts": _hm(t0), "mode": mode,
                               "count": len(bss), "sec": round(time.time() - t0, 1)})
            if len(self.scans) > 60:
                del self.scans[:len(self.scans) - 60]
            self.air_error = ""

    # ---------------- 对外快照 ----------------
    def snapshot(self):
        with self.lock:
            now = time.time()
            cur = self.cur
            air = self.air or {}
            stats = self._stats()
            chan = self._current_channel(cur, air)
            neigh = self._neighbors(cur, air)
            issues = self._issues(cur, chan, neigh, now)
            return {
                "running": self.running,
                "point": self.point,
                "started_at": self.started_at,
                "started_ts": _hm(self.started_at) if self.started_at else "",
                "elapsed_sec": round(now - self.started_at, 1) if self.started_at else 0,
                "poll": self.poll, "air_every": self.air_every, "polls": self.polls,
                "error": self.error, "air_error": self.air_error,
                "current": cur,
                "dwell_sec": round(now - self.since, 1) if (cur and self.since) else None,
                "first": self.first_state,
                "events": list(reversed(self.events[-120:])),
                "event_total": len(self.events),
                "samples": self.samples[-600:],
                "stats": stats,
                "air": {
                    "age_sec": round(now - air["at"], 1) if air.get("at") else None,
                    "scanned_at": air.get("scanned_at"),
                    "mode": air.get("mode") or "",
                    "count": len(air.get("bss") or []),
                    "rollup": air.get("rollup") or {},
                    "channels": chan["series"],
                    "issues": (air.get("issues") or [])[:4],
                    "total_bss": len(air.get("bss") or []),
                    "scans": list(reversed(self.scans[-6:])),
                },
                "channel": chan["current"],
                "neighbors": neigh,
                "issues": issues,
                "verdict": self._verdict(stats, chan["current"], issues),
            }

    def _stats(self):
        ev = self.events
        roams = [e for e in ev if e["kind"] in ("roam", "band-roam")]
        dwells = [e["dwell_sec"] for e in roams if e.get("dwell_sec")]
        weak = [e for e in roams if (e.get("to") or {}).get("rssi") is not None
                and e["to"]["rssi"] <= -70]
        gaps = [e["gap_sec"] for e in roams if e.get("gap_sec")]
        dis = [e for e in ev if e["kind"] == "disconnect"]
        long_dis = [e for e in dis if e.get("gap_sec") and e["gap_sec"] >= 2]
        return {
            "roams": len(roams),
            "band_roams": len([e for e in ev if e["kind"] == "band-roam"]),
            "ssid_switches": len([e for e in ev if e["kind"] == "ssid-switch"]),
            "disconnects": len(dis),
            "dwell_avg": round(sum(dwells) / len(dwells), 1) if dwells else None,
            "dwell_min": min(dwells) if dwells else None,
            "dwell_max": max(dwells) if dwells else None,
            "handoff_avg": round(sum(gaps) / len(gaps), 2) if gaps else None,
            "handoff_max": max(gaps) if gaps else None,
            "weak_handoffs": len(weak),
            "improved_handoffs": len([e for e in roams if (e.get("rssi_delta") or 0) > 0]),
            "unique_aps": len({e["to"]["bssid"] for e in roams if e.get("to")} |
                              ({self.cur["bssid"]} if self.cur else set())),
        }

    def _current_channel(self, cur, air):
        series = air.get("channels") or []
        if not cur:
            return {"current": None, "series": series[:40]}
        band, ch = cur.get("band"), cur.get("channel")
        hit = next((c for c in series if c.get("band") == band and c.get("channel") == ch), None)
        same = [c for c in series if c.get("band") == band]
        rank = None
        if hit and same:
            order = sorted(same, key=lambda c: -(c.get("interference_index") or 0))
            rank = order.index(hit) + 1
        out = {
            "band": band, "channel": ch,
            "index": (hit or {}).get("interference_index"),
            "level": (hit or {}).get("level"),
            "radios": (hit or {}).get("radios_on_channel"),
            "bss": (hit or {}).get("bss_on_channel"),
            "utilization_pct": (hit or {}).get("utilization_pct"),
            "rank": rank, "rank_total": len(same) if same else None,
            "measured": bool(hit),
        }
        return {"current": out, "series": series[:40]}

    def _neighbors(self, cur, air):
        """同 SSID 的其他 BSSID：判断有没有更强邻居没切过去（粘滞证据）。"""
        if not cur:
            return []
        bss = air.get("bss") or []
        out = []
        for b in bss:
            if (b.get("bssid") or "").lower() == cur.get("bssid"):
                continue
            if (b.get("ssid") or "") != (cur.get("ssid") or ""):
                continue
            if b.get("rssi") is None:
                continue
            out.append({"bssid": b["bssid"], "rssi": b["rssi"], "channel": b.get("channel"),
                        "band": b.get("band"), "width_mhz": b.get("width_mhz"),
                        "delta": (b["rssi"] - cur["rssi"]) if cur.get("rssi") is not None else None})
        out.sort(key=lambda x: -(x.get("rssi") or -999))
        return out[:5]

    def _issues(self, cur, chan, neigh, now):
        out = []
        if not cur:
            return [{"severity": "high", "title": "当前未关联 Wi-Fi",
                     "detail": "本机没有连上任何无线网络，漫游无从判定。请先连上被测 SSID 再开始走动采集；"
                               "若使用有线网，需要临时切到无线。"}]
        dwell = round(now - self.since, 1) if self.since else 0
        rssi = cur.get("rssi")
        if rssi is not None and rssi <= -75:
            out.append({"severity": "high", "title": "当前信号 %d dBm，已进入边缘区" % rssi,
                        "detail": "低于 -75 dBm 后速率会明显下滑、语音易断续。这个位置应视为覆盖弱点，"
                                  "建议核对最近 AP 的距离与遮挡，必要时补点。"})
        elif rssi is not None and rssi <= -67:
            out.append({"severity": "medium", "title": "当前信号 %d dBm，开始影响高速率" % rssi,
                        "detail": "-67 dBm 是常见 5G 高吞吐拐点；如果同一位置长期停在这个水平，说明 AP 密度偏低。"})
        best = neigh[0] if neigh else None
        if best and cur.get("rssi") is not None and best.get("rssi") is not None:
            d = best["rssi"] - cur["rssi"]
            if d >= 10 and dwell >= 20:
                out.append({
                    "severity": "high",
                    "title": "粘滞嫌疑：同 SSID 邻居强 %d dB 但未切换" % d,
                    "detail": "当前 %s（%d dBm），另有 %s（%d dBm，ch%s）已强 %d dB，已停留 %d 秒仍未漫游。"
                              "常见原因：未开 802.11k/v、漫游门限过高、网卡驱动漫游策略偏保守。"
                              % (cur.get("bssid"), cur["rssi"], best["bssid"], best["rssi"],
                                 best.get("channel"), d, dwell)})
            elif d >= 15:
                out.append({"severity": "low",
                            "title": "邻居强 %d dB，正在切换到它" % d,
                            "detail": "刚完成切换、或即将切换；继续走动观察是否稳定停在新的 BSSID 上。"})
        if chan and chan.get("index") is not None and chan["index"] >= 55:
            out.append({"severity": "high",
                        "title": "当前信道拥挤（指数 %s / %s）" % (chan["index"], chan.get("level")),
                        "detail": "ch%s 上有 %s 个射频 / %s 个 BSS，利用率 %s。拥挤会放大重传与抖动，"
                                  "与漫游叠加时体感更差。"
                                  % (chan.get("channel"), chan.get("radios"), chan.get("bss"),
                                     ("%s%%" % chan["utilization_pct"]) if chan.get("utilization_pct") is not None else "未上报")})
        if self.air_error:
            out.append({"severity": "low", "title": "周边空口暂不可用",
                        "detail": self.air_error + "；当前连接信息仍然有效，信道拥挤结论会缺失。"})
        age = (now - self.air["at"]) if (self.air and self.air.get("at")) else None
        if age is None or age > max(90.0, self.air_every * 3):
            out.append({"severity": "low", "title": "周边 BSS 数据偏旧",
                        "detail": ("最近一次周边采集在 %s 秒前。" % int(age) if age is not None
                                   else "还没有取到周边 BSS。")
                                  + "信道拥挤结论会滞后；需要最新值时点「刷新周边」做一次主动扫描。"})
        return out

    def _verdict(self, stats, chan, issues):
        lv = 100.0
        if stats["disconnects"]:
            lv -= min(30, stats["disconnects"] * 8)
        if stats["weak_handoffs"]:
            lv -= min(24, stats["weak_handoffs"] * 8)
        if stats["handoff_max"] and stats["handoff_max"] > 3:
            lv -= 12
        if chan and chan.get("index") is not None:
            lv -= min(25, max(0.0, (chan["index"] - 35) / 65.0 * 25))
        if any(i["severity"] == "high" for i in issues):
            lv -= 10
        lv = int(max(0, min(100, round(lv))))
        grade = "优" if lv >= 85 else "良" if lv >= 70 else "中" if lv >= 55 else "差"
        return {"score": lv, "grade": grade,
                "text": ("漫游体验 %s：%d 次漫游、%d 次掉线、切换后弱信号 %d 次"
                         % (grade, stats["roams"], stats["disconnects"], stats["weak_handoffs"]))}

    # ---------------- 导出 ----------------
    def to_csv(self):
        with self.lock:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["采集点", self.point, "开始", _hm(self.started_at) if self.started_at else ""])
            w.writerow([])
            w.writerow(["时间", "类型", "原SSID", "原BSSID", "原信道", "原频段", "原RSSI",
                        "新SSID", "新BSSID", "新信道", "新频段", "新RSSI",
                        "驻留秒", "空窗秒", "RSSI变化", "说明"])
            for e in self.events:
                a, b = e.get("from") or {}, e.get("to") or {}
                w.writerow([e["ts"], e["label"],
                            a.get("ssid", ""), a.get("bssid", ""), a.get("channel", ""),
                            a.get("band", ""), a.get("rssi", ""),
                            b.get("ssid", ""), b.get("bssid", ""), b.get("channel", ""),
                            b.get("band", ""), b.get("rssi", ""),
                            e.get("dwell_sec", ""), e.get("gap_sec", ""),
                            e.get("rssi_delta", ""), e.get("note", "")])
            w.writerow([])
            w.writerow(["口径", "RSSI 由 netsh 信号百分比换算，分辨率约 2 dB；"
                               "切换空窗受轮询间隔限制，为观测值不是空口时延；周边 BSS 为系统扫描缓存"])
            return "\ufeff" + buf.getvalue()
