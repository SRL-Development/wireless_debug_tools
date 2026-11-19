"""空口质量与干扰评估：把真实扫描到的 BSS 列表翻译成可判读的结论。

关键建模约定（都在下方 assumptions 里回给前端，避免"黑箱数字"）：
1. 同一物理射频会广播多个 SSID，BSSID 只有最后一字节不同。因此按 BSSID 前 5 字节
   归并为「射频(Radio)」，干扰只按射频计一次，否则一台 AP 挂 5 个 SSID 会被算成 5 个干扰源。
2. 「噪声底」只放热噪声与带宽，不放干扰；干扰单独用 SINR 表达。
   SNR = RSSI - 热噪声； SINR = RSSI - 10log10(P_noise + Σ P_i × 占空比)
   占空比取 AP 上报的 BSS Load utilization，缺失按 30% 估。
3. 2.4G 邻频按频谱掩码近似加权：±1:-3 dB / ±2:-10 dB / ±3:-20 dB / ±4:-30 dB。
4. 单台笔记本是单点测量，换位置结果会变。
"""
import math

CH24 = list(range(1, 14))
CH5 = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120,
       124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165]
CH6 = [1, 5, 9, 13, 17, 21, 25, 29, 33, 37, 41, 45, 49, 53, 57, 61,
       65, 69, 73, 77, 81, 85, 89, 93, 97, 101, 105, 109, 113,
       117, 121, 125, 129, 133, 137, 141, 145, 149, 153, 157, 161,
       165, 169, 173, 177, 181, 185, 189, 193, 197, 201, 205, 209, 213, 217, 221, 225, 229, 233]

THERMAL_NOISE_20MHZ = -94.0
WIDTH_NOISE = {20: 0.0, 40: 3.0, 80: 6.0, 160: 9.0}
ADJ_WEIGHT = {1: 0.5, 2: 0.1, 3: 0.01, 4: 0.001}
DEFAULT_DUTY = 0.30


def _freq_mhz(ch, band):
    if ch is None:
        return None
    if band == "6" or ch > 177:
        return 5950 + 5 * ch
    if ch == 14:
        return 2484.0
    if ch <= 14:
        return 2412.0 + 5 * (ch - 1)
    return 5000.0 + 5 * ch


def _span(ch, width, band):
    """该 BSS 实际占用的频率区间 (lo, hi)，单位 MHz。"""
    f = _freq_mhz(ch, band)
    if f is None:
        return None
    w = width or 20
    return (f - w / 2.0, f + w / 2.0)


def _overlap_ratio(a, b):
    """两个频段的重叠比例（相对较窄者）。"""
    if not a or not b:
        return 0.0
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if hi <= lo:
        return 0.0
    narrower = min(a[1] - a[0], b[1] - b[0])
    return 0.0 if narrower <= 0 else min(1.0, (hi - lo) / narrower)


def _covers(radio_span, ch, band, min_ratio=0.5):
    """该射频是否覆盖到指定 20 MHz 参考信道。"""
    ref = _span(ch, 20, band)
    return _overlap_ratio(radio_span, ref) >= min_ratio


def _noise_floor(width):
    return THERMAL_NOISE_20MHZ + WIDTH_NOISE.get(width or 20, 0.0)


def _airtime_pct(b):
    u = (b.get("bss_load") or {}).get("utilization")
    return None if u is None else round(min(100.0, u * 100.0 / 255.0), 1)


def _duty(b):
    u = _airtime_pct(b)
    return DEFAULT_DUTY if u is None else max(0.02, u / 100.0)


def _radio_key(bssid, band=""):
    """BSSID 前 5 字节 + 频段 → 同一物理射频。
    同一台 AP 的 2.4G 与 5G 是两个独立射频，必须分开计。"""
    parts = (bssid or "").split(":")
    base = ":".join(parts[:5]) if len(parts) >= 5 else (bssid or "")
    return base + ("@" + band if band else "")


def _band_channels(band):
    return {"2.4": CH24, "5": CH5, "6": CH6}.get(band, [])


def _build_radios(bss):
    """把 BSS 归并为射频：干扰源按射频只算一次。"""
    radios = {}
    for b in bss:
        k = _radio_key(b.get("bssid"), b.get("band") or "")
        r = radios.setdefault(k, {
            "key": k, "bssids": [], "ssids": set(), "channel": b.get("channel"),
            "band": b.get("band"), "width_mhz": b.get("width_mhz") or 20,
            "rssi": b.get("rssi"), "utilization_pct": None, "stations": None,
            "span": _span(b.get("channel"), b.get("width_mhz"), b.get("band")),
        })
        r["bssids"].append(b.get("bssid"))
        if b.get("ssid"):
            r["ssids"].add(b["ssid"])
        if (b.get("rssi") or -999) > (r["rssi"] or -999):
            r["rssi"] = b.get("rssi")
            r["channel"] = b.get("channel")
            r["band"] = b.get("band")
            r["width_mhz"] = b.get("width_mhz") or 20
            r["span"] = _span(b.get("channel"), b.get("width_mhz"), b.get("band"))
        u = _airtime_pct(b)
        if u is not None:
            r["utilization_pct"] = max(r["utilization_pct"] or 0, u)
        st = (b.get("bss_load") or {}).get("stations")
        if st is not None:
            r["stations"] = max(r["stations"] or 0, st)
    return list(radios.values())


def _interference(target, radios, band):
    """返回 (同频射频数, 邻频射频数, 干扰功率 dBm, 热噪声+干扰合成底噪 dBm)。"""
    tspan = target.get("span")
    p_noise = 10 ** (_noise_floor(target.get("width_mhz")) / 10.0)
    p_int = 0.0
    co = adj = 0
    for r in radios:
        if r["key"] == target["key"] or r.get("rssi") is None:
            continue
        ospan = r.get("span")
        ratio = _overlap_ratio(tspan, ospan)
        w = 0.0
        if ratio >= 0.5:
            w, co = 1.0, co + 1
        elif band == "2.4":
            # 2.4G 邻频：按信道距离给频谱掩码权重
            d = abs((target.get("channel") or 0) - (r.get("channel") or 0))
            w = ADJ_WEIGHT.get(d, 0.0)
            if w:
                adj += 1
        elif ratio > 0:
            w = ratio
            adj += 1
        if w:
            p_int += (10 ** (r["rssi"] / 10.0)) * w * _duty_radio(r)
    p_int_total = max(p_int, 0.0)
    int_dbm = round(10 * math.log10(p_int_total), 1) if p_int_total > 0 else None
    combined = round(10 * math.log10(p_noise + p_int_total), 1)
    return co, adj, int_dbm, combined


def _duty_radio(r):
    u = r.get("utilization_pct")
    return DEFAULT_DUTY if u is None else max(0.02, u / 100.0)


def analyze(bss_list, point_label=""):
    bss = [b for b in (bss_list or []) if b.get("rssi") is not None]
    if not bss:
        return {"ok": False, "error": "没有可分析的 BSS 数据", "bss": [], "ssids": [],
                "channels": [], "radios": [], "rollup": {}, "issues": [], "recommend": {}}

    radios = _build_radios(bss)

    # ---------------- 按 SSID 聚合 ----------------
    ssid_map = {}
    for b in bss:
        name = b.get("ssid") or ""
        key = name if name else "__hidden__"
        e = ssid_map.setdefault(key, {
            "ssid": name, "hidden": not bool(name), "bssids": [], "radios": set(),
            "bands": set(), "channels": set(), "rssi_list": [],
            "security": set(), "widths": set(), "phy": set(),
            "stations": [], "utilization": [], "best": None,
        })
        e["bssids"].append(b.get("bssid"))
        e["radios"].add(_radio_key(b.get("bssid"), b.get("band") or ""))
        e["bands"].add(b.get("band"))
        if b.get("channel"):
            e["channels"].add(b.get("channel"))
        e["rssi_list"].append(b.get("rssi"))
        if e["best"] is None or (b.get("rssi") or -999) > (e["best"].get("rssi") or -999):
            e["best"] = b
        e["security"].add(b.get("security") or "")
        e["widths"].add(b.get("width_mhz") or 20)
        e["phy"].add(b.get("phy") or "")
        st = (b.get("bss_load") or {}).get("stations")
        if st is not None:
            e["stations"].append(st)
        u = _airtime_pct(b)
        if u is not None:
            e["utilization"].append(u)

    ssids = []
    for key, e in ssid_map.items():
        rep = e["best"]
        rk = _radio_key(rep.get("bssid"), rep.get("band") or "")
        radio = next((r for r in radios if r["key"] == rk), None) or {
            "key": rk, "span": _span(rep.get("channel"), rep.get("width_mhz"), rep.get("band")),
            "rssi": rep.get("rssi"), "width_mhz": rep.get("width_mhz") or 20,
            "utilization_pct": None,
        }
        co, adj, int_dbm, combined = _interference(radio, radios, rep.get("band"))
        noise = _noise_floor(radio.get("width_mhz"))
        rssi_best = max(e["rssi_list"])
        snr = round(rssi_best - noise, 1)
        sinr = round(rssi_best - combined, 1)
        util = max(e["utilization"]) if e["utilization"] else None
        score = _quality_score(sinr, co, util, rssi_best)
        ssids.append({
            "ssid": e["ssid"],
            "label": e["ssid"] if e["ssid"] else "隐藏 SSID（%d 个 BSS）" % len(e["bssids"]),
            "hidden": e["hidden"],
            "bss_count": len(e["bssids"]),
            "radio_count": len(e["radios"]),
            "bssids": sorted(x for x in e["bssids"] if x),
            "bands": sorted(x for x in e["bands"] if x),
            "channels": sorted(e["channels"]),
            "rssi_best": rssi_best,
            "rssi_avg": round(sum(e["rssi_list"]) / len(e["rssi_list"]), 1),
            "snr_db": snr, "sinr_db": sinr,
            "noise_floor_dbm": round(noise, 1),
            "interference_dbm": int_dbm,
            "co_channel": co, "adjacent": adj,
            "security": " / ".join(sorted(x for x in e["security"] if x)) or "未知",
            "security_score": _security_score(e["security"]),
            "widths": sorted(e["widths"]),
            "phy": sorted(x for x in e["phy"] if x),
            "stations": max(e["stations"]) if e["stations"] else None,
            "utilization_pct": util,
            "quality_score": score,
            "quality_grade": _grade(score),
        })
    # 隐藏 SSID 排后面，其余按信号强度
    ssids.sort(key=lambda x: (x["hidden"], -x["rssi_best"], x["ssid"]))

    # ---------------- 按信道聚合（干扰视图，按频段隔离） ----------------
    channels = []
    for band in ("2.4", "5", "6"):
        for ch in _band_channels(band):
            on = [b for b in bss if b.get("band") == band and b.get("channel") == ch]
            covering = [r for r in radios if r.get("band") == band
                        and _covers(r.get("span"), ch, band)]
            if not on and not covering and band != "2.4":
                continue
            util = [u for u in (_airtime_pct(b) for b in on) if u is not None]
            strongest = max((b.get("rssi") for b in on if b.get("rssi") is not None), default=None)
            active = [r for r in radios if r.get("band") == band
                      and _covers(r.get("span"), ch, band)
                      and (r.get("rssi") or -100) > -85]
            co_pairs = len(active) * (len(active) - 1) // 2 if len(active) > 1 else 0
            load = max(util) if util else None
            idx = 0.0
            idx += min(1.0, len(active) / 5.0) * 45
            idx += min(1.0, co_pairs / 10.0) * 20
            idx += (min(1.0, load / 100.0) * 35) if load is not None else (min(1.0, len(active) / 5.0) * 20)
            channels.append({
                "band": band, "channel": ch,
                "bss_on_channel": len(on),
                "radios_on_channel": len(covering),
                "active_radios": len(active),
                "co_pairs": co_pairs,
                "strongest_rssi": strongest,
                "utilization_pct": load,
                "interference_index": round(min(100.0, idx), 0),
                "level": _level(idx),
            })

    # ---------------- 整体 ----------------
    best_rssi = max((b.get("rssi") for b in bss if b.get("rssi") is not None), default=None)
    cover_score = 0.0 if best_rssi is None else max(0.0, min(1.0, (best_rssi + 90) / 40.0))
    chan_idx = [c["interference_index"] for c in channels if c["bss_on_channel"] or c["radios_on_channel"]]
    intf_score = 0.0 if not chan_idx else max(0.0, 1.0 - (sum(chan_idx) / len(chan_idx)) / 100.0)
    open_cnt = sum(1 for b in bss if (b.get("security") or "").startswith("开放"))
    sec_score = (1.0 - open_cnt / len(bss)) if bss else 0.0
    n24 = sum(1 for b in bss if b.get("band") == "2.4")
    n24_radio = len([r for r in radios if r.get("band") == "2.4"])
    n5 = sum(1 for b in bss if b.get("band") == "5")
    n6 = sum(1 for b in bss if b.get("band") == "6")
    n5_radio = len([r for r in radios if r.get("band") == "5"])
    density_score = max(0.0, min(1.0, 1.0 - max(0, n24_radio - 3) / 6.0))
    hidden_cnt = sum(1 for b in bss if b.get("hidden"))
    overall = round(100 * (0.35 * cover_score + 0.35 * intf_score +
                           0.15 * sec_score + 0.15 * density_score), 0)

    recommend = {}
    for band in ("2.4", "5", "6"):
        cand = [c for c in channels if c["band"] == band]
        if not cand:
            continue
        cand.sort(key=lambda c: (c["interference_index"], c["radios_on_channel"], c["channel"]))
        recommend[band] = [{"channel": c["channel"], "interference_index": c["interference_index"],
                            "radios_on_channel": c["radios_on_channel"],
                            "utilization_pct": c["utilization_pct"]} for c in cand[:3]]

    issues = []
    worst = max((c for c in channels if c["bss_on_channel"]), key=lambda c: c["interference_index"], default=None)
    if worst and worst["interference_index"] >= 55:
        better = recommend.get(worst["band"]) or []
        tgt = ("信道 %s" % better[0]["channel"]) if better else "更空闲的信道"
        issues.append({"severity": "high",
                       "title": "%s GHz 信道 %d 干扰偏高（指数 %d / %s）" % (
                           worst["band"], worst["channel"], worst["interference_index"], worst["level"]),
                       "detail": "该信道有 %d 个射频、%d 个 BSS、AP 上报利用率 %s；建议把本层 AP 迁到 %s"
                                 % (worst["radios_on_channel"], worst["bss_on_channel"],
                                    ("%s%%" % worst["utilization_pct"]) if worst["utilization_pct"] is not None else "未上报",
                                    tgt)})
    if n24_radio >= 5:
        issues.append({"severity": "medium", "title": "2.4 GHz 射频过密（%d 个射频 / %d 个 BSS）" % (n24_radio, n24),
                       "detail": "2.4G 只有 1/6/11 三个互不重叠信道，射频超过 4 个必然同频；"
                                 "建议双频终端优先引导 5G，2.4G 只留 IoT 与访客"})
    if open_cnt:
        issues.append({"severity": "high" if open_cnt >= 3 else "medium",
                       "title": "存在 %d 个开放 / 未加密 BSS" % open_cnt,
                       "detail": "开放网络可被旁路监听，也容易被同名热点仿冒；建议改 WPA2/3-Enterprise 或至少 WPA2-PSK"})
    if hidden_cnt >= 3:
        issues.append({"severity": "low", "title": "发现 %d 个隐藏 SSID 的 BSS" % hidden_cnt,
                       "detail": "隐藏 SSID 不提升安全性（空口仍可被动发现与关联探测），但会干扰信道规划；建议统一取消隐藏并靠准入控制"})
    wide = [b for b in bss if (b.get("width_mhz") or 20) >= 80]
    if wide:
        issues.append({"severity": "medium", "title": "%d 个 BSS 使用 80 MHz 及以上带宽" % len(wide),
                       "detail": "80/160 MHz 占用更多频谱、把噪声底抬高 6-9 dB；高密场景建议 5G 统一 40 MHz，只在低密区开 80"})

    return {
        "ok": True,
        "point": point_label,
        "bss": bss,
        "radios": [dict(r, ssids=sorted(r["ssids"]), span=list(r["span"]) if r.get("span") else None)
                   for r in radios],
        "ssids": ssids,
        "channels": channels,
        "rollup": {
            "bss_total": len(bss),
            "radio_total": len(radios),
            "ssid_total": len([k for k in ssid_map if k != "__hidden__"]),
            "hidden_bss": hidden_cnt,
            "open_bss": open_cnt,
            "bss_24": n24, "radios_24": n24_radio,
            "bss_5": n5, "radios_5": n5_radio,
            "bss_6": n6,
            "best_rssi": best_rssi,
            "strongest_ssid": ssids[0]["label"] if ssids else None,
            "overall_score": overall, "overall_grade": _grade(overall),
            "coverage_score": round(cover_score * 100),
            "interference_score": round(intf_score * 100),
            "security_score": round(sec_score * 100),
            "density_score": round(density_score * 100),
            "avg_channel_index": round(sum(chan_idx) / len(chan_idx), 0) if chan_idx else None,
            "worst_channel": ({"band": worst["band"], "channel": worst["channel"],
                               "index": worst["interference_index"]} if worst else None),
        },
        "recommend": recommend,
        "issues": issues,
        "assumptions": [
            "同一射频广播多个 SSID，BSSID 仅末字节不同：按 BSSID 前 5 字节归并，干扰只计一次",
            "热噪声底 = -174 dBm/Hz + 带宽 + 7 dB 噪声系数；20/40/80/160 MHz 分别为 -94/-91/-88/-85 dBm",
            "SINR = RSSI - 10log10(热噪声 + Σ干扰功率×占空比)；占空比取 AP 上报 BSS Load，缺失按 30% 估",
            "2.4G 邻频加权 ±1:-3 dB / ±2:-10 dB / ±3:-20 dB / ±4:-30 dB（频谱掩码近似）",
            "单次扫描是单点测量，换位置结果会变；整层判定需多点采集",
        ],
    }


def _quality_score(sinr, co, util, rssi):
    """质量分：SINR 55% + 同频争用 20% + 信道利用率 15% + 绝对信号 10%"""
    if rssi is None:
        return 0
    s_sinr = max(0.0, min(1.0, (sinr - 3) / 27.0))
    s_co = max(0.0, 1.0 - min(co, 6) / 6.0)
    s_util = 1.0 if util is None else max(0.0, 1.0 - util / 100.0)
    s_rssi = max(0.0, min(1.0, (rssi + 85) / 35.0))
    return round(100 * (0.55 * s_sinr + 0.20 * s_co + 0.15 * s_util + 0.10 * s_rssi), 0)


def _security_score(sec_set):
    s = " ".join(sorted(x for x in sec_set if x))
    if not s or "开放" in s:
        return 0
    if "Enterprise" in s:
        return 100
    if "WPA3" in s or "SAE" in s:
        return 95
    if "WPA2" in s or "WPA" in s:
        return 75
    return 40


def _grade(score):
    if score >= 85:
        return "优"
    if score >= 70:
        return "良"
    if score >= 55:
        return "中"
    if score >= 40:
        return "差"
    return "很差"


def _level(idx):
    if idx >= 75:
        return "极高"
    if idx >= 55:
        return "高"
    if idx >= 35:
        return "中"
    if idx >= 15:
        return "低"
    return "极低"
