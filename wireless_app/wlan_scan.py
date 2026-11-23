"""真实空口扫描：Windows Native WiFi API（wlanapi.dll）只读调用。
不连接、不修改任何无线配置，只做 WlanScan + 读取 BSS 列表。
"""
import ctypes
import ctypes.wintypes as wt
import time

wlanapi = ctypes.WinDLL("wlanapi.dll")

# 必须显式声明签名，否则 64 位下 HANDLE / 指针会被截断成 int
_GUID_P = ctypes.c_void_p
wlanapi.WlanOpenHandle.argtypes = [wt.DWORD, ctypes.c_void_p, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.HANDLE)]
wlanapi.WlanOpenHandle.restype = wt.DWORD
wlanapi.WlanCloseHandle.argtypes = [wt.HANDLE, ctypes.c_void_p]
wlanapi.WlanCloseHandle.restype = wt.DWORD
wlanapi.WlanEnumInterfaces.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.DWORD)]
wlanapi.WlanEnumInterfaces.restype = wt.DWORD
wlanapi.WlanScan.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
wlanapi.WlanScan.restype = wt.DWORD
wlanapi.WlanGetNetworkBssList.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                          wt.BOOL, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
wlanapi.WlanGetNetworkBssList.restype = wt.DWORD
wlanapi.WlanFreeMemory.argtypes = [ctypes.c_void_p]
wlanapi.WlanFreeMemory.restype = None

ERROR_SUCCESS = 0
WLAN_MAX_NAME_LENGTH = 256
DOT11_SSID_MAX_LENGTH = 32
DOT11_BSSID_LIST_REVISION_1 = 1
DOT11_BSS_TYPE_ANY = 3
WLAN_AVAILABLE_NETWORK_INCLUDE_ALL_ADHOC_PROFILES = 0x00000001

# WLAN_BSS_ENTRY 内存布局（含尾部 IE blob）
class DOT11_SSID(ctypes.Structure):
    _fields_ = [("uSSIDLength", wt.ULONG),
                ("ucSSID", ctypes.c_ubyte * DOT11_SSID_MAX_LENGTH)]


class DOT11_MAC_ADDRESS(ctypes.Structure):
    _fields_ = [("ucAddress", ctypes.c_ubyte * 6)]


class WLAN_RATE_SET(ctypes.Structure):
    _fields_ = [("uRateSetLength", wt.ULONG),
                ("usRateSet", ctypes.c_ushort * 126)]


class WLAN_BSS_ENTRY(ctypes.Structure):
    """wlanapi.h WLAN_BSS_ENTRY.

    Keep the declaration complete and derive offsets from ctypes. The previous
    hand-maintained offsets accidentally read dot11BssPhyType as link quality
    and shifted the beacon/capability fields.
    """
    _fields_ = [
        ("dot11Ssid", DOT11_SSID),
        ("uPhyId", wt.ULONG),
        ("dot11Bssid", DOT11_MAC_ADDRESS),
        ("dot11BssType", ctypes.c_uint),
        ("dot11BssPhyType", ctypes.c_uint),
        ("lRssi", wt.LONG),
        ("uLinkQuality", wt.ULONG),
        ("bInRegDomain", wt.BOOLEAN),
        ("usBeaconPeriod", ctypes.c_ushort),
        ("ullTimestamp", ctypes.c_ulonglong),
        ("ullHostTimestamp", ctypes.c_ulonglong),
        ("usCapabilityInformation", ctypes.c_ushort),
        ("ulChCenterFrequency", wt.ULONG),      # kHz
        ("wlanRateSet", WLAN_RATE_SET),
        ("ulIeOffset", wt.ULONG),               # 相对本结构起点
        ("ulIeSize", wt.ULONG),
    ]


class WLAN_BSS_LIST(ctypes.Structure):
    _fields_ = [("dwTotalSize", wt.ULONG),
                ("dwNumberOfItems", wt.ULONG)]


# 从官方结构声明派生偏移，避免字段顺序或对齐规则被手工常量掩盖。
OFF_SSID_LEN = WLAN_BSS_ENTRY.dot11Ssid.offset + DOT11_SSID.uSSIDLength.offset
OFF_SSID = WLAN_BSS_ENTRY.dot11Ssid.offset + DOT11_SSID.ucSSID.offset
OFF_BSSID = WLAN_BSS_ENTRY.dot11Bssid.offset
OFF_LINK_QUALITY = WLAN_BSS_ENTRY.uLinkQuality.offset
OFF_RSSI = WLAN_BSS_ENTRY.lRssi.offset
OFF_BEACON_PERIOD = WLAN_BSS_ENTRY.usBeaconPeriod.offset
OFF_CAPABILITY = WLAN_BSS_ENTRY.usCapabilityInformation.offset
OFF_FREQ_KHZ = WLAN_BSS_ENTRY.ulChCenterFrequency.offset
OFF_IE_OFFSET = WLAN_BSS_ENTRY.ulIeOffset.offset
OFF_IE_SIZE = WLAN_BSS_ENTRY.ulIeSize.offset
BSS_ENTRY_SIZE = ctypes.sizeof(WLAN_BSS_ENTRY)
MAX_IE_BLOB = 2324       # wlanapi.h 对单个 IE blob 的公开上限

PHY_MAP = {1: "FHSS", 2: "DSSS", 3: "IR", 4: "OFDM", 5: "HR-DSSS",
           6: "ERP", 7: "802.11n(HT)", 8: "802.11ac(VHT)",
           9: "802.11ax(HE)", 10: "802.11be(EHT)"}


def _u32(buf, off):
    return int.from_bytes(buf[off:off + 4], "little", signed=False)


def _i32(buf, off):
    return int.from_bytes(buf[off:off + 4], "little", signed=True)


def _u16(buf, off):
    return int.from_bytes(buf[off:off + 2], "little", signed=False)


def _u64(buf, off):
    return int.from_bytes(buf[off:off + 8], "little", signed=False)


class WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [("InterfaceGuid", ctypes.c_ubyte * 16),
                ("strInterfaceDescription", wt.WCHAR * 256),
                ("isState", ctypes.c_uint)]


class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [("dwNumberOfItems", wt.DWORD),
                ("dwIndex", wt.DWORD),
                ("InterfaceInfo", WLAN_INTERFACE_INFO * 1)]


def _freq_to_channel(freq_khz):
    if not freq_khz:
        return None
    mhz = freq_khz / 1000.0
    if 2412 <= mhz <= 2484:
        if mhz == 2484:
            return 14
        return int(round((mhz - 2407) / 5.0))
    if 5000 <= mhz < 5925:
        return int(round((mhz - 5000) / 5.0))
    if 5950 <= mhz <= 7125:
        return int(round((mhz - 5950) / 5.0))
    return None


def channel_to_band(ch):
    if ch is None:
        return ""
    if 1 <= ch <= 14:
        return "2.4"
    if 32 <= ch <= 177:
        return "5"
    if 1 <= ch <= 233 and ch > 177:
        return "6"
    return ""


def _band_of(ch, freq_khz):
    mhz = (freq_khz or 0) / 1000.0
    if 5925 <= mhz <= 7125:
        return "6"
    if mhz >= 4900:
        return "5"
    if mhz:
        return "2.4"
    return channel_to_band(ch)


OUI_NAMES = {
    "0050f2": "Microsoft / WPA", "506f9a": "Wi-Fi Alliance",
    "000b86": "Aruba / HPE", "000b85": "Cisco", "004096": "Cisco Aironet",
    "0014f1": "Cisco", "001ebe": "Cisco", "002414": "Cisco",
    "001a1e": "Ruckus", "3c2af4": "Ruckus", "00186e": "Ubiquiti",
    "00259c": "Ubiquiti", "00156d": "Ubiquiti", "000c29": "VMware",
    "001b21": "Intel", "001e67": "Intel", "005f73": "Huawei",
    "0019c6": "Huawei", "00e0fc": "Huawei", "f4c7a8": "Huawei",
    "00074f": "Zebra / Symbol", "001cf0": "D-Link",
    "0022b0": "D-Link", "001195": "SonicWall",
    "000f7d": "Zyxel", "0013f7": "Zyxel", "00a0c5": "Netgear",
    "0018e7": "Tp-Link", "50c7bf": "Tp-Link", "001d0f": "Tp-Link",
    "0026ce": "Extreme", "000e58": "Meraki / Cisco",
}


def _vendor_names(ouis):
    return [OUI_NAMES.get(o, o[:2] + ":" + o[2:4] + ":" + o[4:6]) for o in sorted(ouis)]


def _parse_ies(blob):
    """解析信息元素：DS / HT / VHT / HE / RSN / WPA / 扩展能力 / 厂商 / BSS Load / 漫游与安全能力。

    v0.13：新增 PMF、SAE、OWE、FT(802.11r)、RM(802.11k)、BTM(802.11v)、MBSSID、
    WMM、DTIM、BSS Color、RSN 加密套件与厂商名。所有能力均由 Beacon/Probe IE 位推断，
    厂商实现可能有差异，界面上会如实标注。
    """
    out = {"channels": [], "width_mhz": None, "security": set(), "vendor_ouis": set(),
           "country": "", "ssid_in_ie": None, "rsn_akms": set(), "bss_load": None,
           "he": False, "ht": False, "vht": False, "he_6ghz": False, "mbo": False,
           "pmf_required": False, "pmf_capable": False, "sae": False, "owe": False,
           "ft": False, "ft_sae": False, "wpa3_enterprise": False, "rsn": False,
           "mobility_domain": False, "rm_enabled": False, "btm": False,
           "mbssid": False, "wmm": False, "dtim_period": None, "bss_color": None,
           "ciphers": set(), "group_cipher": "", "ext_caps": None}
    i = 0
    n = len(blob)
    while i + 2 <= n:
        eid = blob[i]
        ln = blob[i + 1]
        body = blob[i + 2:i + 2 + ln]
        i += 2 + ln
        if len(body) < ln:
            break
        if eid == 3 and ln >= 1:            # DS Parameter Set
            out["channels"].append(body[0])
        elif eid == 61 and ln >= 3:          # HT Operation
            out["width_mhz"] = 40 if (body[1] & 0x03) else 20
        elif eid == 191 and ln >= 3:         # VHT Operation
            out["vht"] = True
            w = body[0]
            out["width_mhz"] = {0: 20, 1: 80, 2: 160, 3: 160}[w] if w in (0, 1, 2, 3) else out["width_mhz"]
            if w == 0 and out["width_mhz"] in (None, 20):
                out["width_mhz"] = 20 if body[1] == 0 else 80
        elif eid == 255 and ln >= 6:         # Extension: HE Operation
            if body[0] == 36:
                out["he"] = True
                if ln >= 4:
                    out["bss_color"] = body[2] & 0x3F
            if body[0] == 36 and ln >= 7:
                out["he_6ghz"] = bool(body[6] & 0x02)
        elif eid == 54 and ln >= 3:          # Mobility Domain → 802.11r FT
            out["mobility_domain"] = True
        elif eid == 70 and ln >= 1:          # RM Enabled Capabilities → 802.11k
            out["rm_enabled"] = True
        elif eid == 127 and ln >= 1:         # Extended Capabilities（位定义见 802.11-2020）
            out["ext_caps"] = body[:8].hex()
            bits = int.from_bytes(body[:8], "little")
            out["btm"] = bool(bits & (1 << 19))       # BSS Transition Management (802.11v)
            out["mbssid"] = bool(bits & (1 << 22))    # Multiple BSSID
        elif eid == 71:                       # Multiple BSSID
            out["mbssid"] = True
        elif eid == 5 and ln >= 2:            # TIM → DTIM Period
            out["dtim_period"] = body[1]
        elif eid == 45 and ln >= 12:         # HT Capabilities
            out["ht"] = True
            if body[1] & 0x02:               # Supported Channel Width Set
                out["width_mhz"] = max(out["width_mhz"] or 20, 40)
        elif eid == 48:                      # RSN
            out["rsn"] = True
            out["security"].add("WPA2/3")
            if ln >= 2:
                cap = int.from_bytes(body[0:2], "little")
                out["pmf_required"] = bool(cap & (1 << 6))   # MFPR
                out["pmf_capable"] = bool(cap & (1 << 7))    # MFPC
            if ln >= 6:
                out["group_cipher"] = _cipher_name(body[2:6])
            j = 6
            if ln >= 8:
                pcount = int.from_bytes(body[6:8], "little") if ln >= 8 else 0
                for pi in range(min(pcount, 4)):
                    off = 8 + pi * 4
                    if off + 4 <= ln:
                        c = _cipher_name(body[off:off + 4])
                        if c:
                            out["ciphers"].add(c)
                j = 8 + pcount * 4
                if ln >= j + 2:
                    acount = int.from_bytes(body[j:j + 2], "little")
                    k = j + 2
                    for _ in range(min(acount, 8)):
                        if k + 4 <= ln:
                            suite = body[k:k + 4]
                            out["rsn_akms"].add(suite.hex())
                            tail = suite[-1]
                            if tail == 8:
                                out["sae"] = True
                            elif tail == 9:
                                out["ft_sae"] = True
                                out["ft"] = True
                            elif tail in (3, 4):
                                out["ft"] = True
                            elif tail == 18:
                                out["owe"] = True
                            elif tail in (11, 12, 13):
                                out["wpa3_enterprise"] = True
                            k += 4
        elif eid == 221 and ln >= 4:         # Vendor Specific
            oui = body[:3]
            out["vendor_ouis"].add(oui.hex())
            if oui == b"\x00\x50\xf2" and ln >= 8:      # WPA1
                out["security"].add("WPA")
                if body[4:8] == b"\x00\x50\xf2\x01":
                    out["security"].add("WPA-PSK")
                elif body[4:8] == b"\x00\x50\xf2\x02":
                    out["security"].add("WPA-Enterprise")
            if oui == b"\x00\x50\xf2" and ln >= 5 and body[3] == 2:   # WMM / WMM-PS
                out["wmm"] = True
            if oui == b"\x50\x6f\x9a" and ln >= 10:     # WFA OUI
                sub = body[3]
                if sub == 0x10:
                    out["mbo"] = True
        elif eid == 11 and ln >= 1:          # BSS Load
            if ln >= 5:
                out["bss_load"] = {
                    "stations": int.from_bytes(body[0:2], "little"),
                    "utilization": body[2],
                }
        elif eid == 7:                       # Country
            if ln >= 3:
                out["country"] = body[0:3].decode("ascii", "ignore").strip("\x00 ")
        elif eid == 0:                       # SSID element（隐藏探测响应里可能带真名）
            try:
                nm = body.decode("utf-8", "ignore").strip("\x00")
                if nm:
                    out["ssid_in_ie"] = nm
            except Exception:
                pass
    return out


def _cipher_name(suite):
    if len(suite) < 4:
        return ""
    t = suite[3]
    return {1: "WEP-40", 2: "TKIP", 4: "CCMP", 5: "WEP-104",
            8: "GCMP", 9: "GCMP-256", 10: "CCMP-256"}.get(t, "")


def _phy_from_ies(ies, width):
    if ies.get("he_6ghz") or ies.get("he"):
        return "802.11ax(HE)"
    if width and width >= 80:
        return "802.11ac(VHT)"
    if ies.get("ht"):
        return "802.11n(HT)"
    return "802.11a/g"


def _security_label(ies):
    """按 RSN AKM 末字节精确判定（避免 00-0F-AC:12 的 OWE 被 endswith('02') 误判成 PSK）。"""
    s = ies["security"]
    tails = {a[-2:].lower() for a in ies["rsn_akms"] if len(a) >= 2}
    ent = bool(tails & {"01", "03", "05", "0b", "0c", "0d"}) or "WPA-Enterprise" in s
    psk = bool(tails & {"02", "04", "06"}) or "WPA-PSK" in s
    sae = bool(tails & {"08", "09"})
    owe = "12" in tails
    suiteb = bool(tails & {"0b", "0c", "0d"})
    parts = []
    if sae:
        parts.append("WPA3-SAE")
    if owe:
        parts.append("OWE 增强开放")
    if suiteb:
        parts.append("Suite-B 企业")
    elif ent:
        parts.append("WPA2/3-Enterprise")
    if psk:
        parts.append("WPA2-PSK")
    if not parts:
        if "WPA2/3" in s or "WPA" in s:
            parts.append("WPA2-PSK")
    if not parts:
        return "开放 / 未知"
    return " · ".join(dict.fromkeys(parts))


def capability_flags(ies, band=""):
    """把 IE 能力位翻译成人话标签；用于 AP 能力矩阵与审计规则。"""
    f = []
    if ies.get("sae") or ies.get("ft_sae"):
        f.append("WPA3-SAE")
    if ies.get("owe"):
        f.append("OWE")
    if ies.get("pmf_required"):
        f.append("PMF 强制")
    elif ies.get("pmf_capable"):
        f.append("PMF 可选")
    elif ies.get("rsn"):
        f.append("PMF 缺失")
    if ies.get("mobility_domain") or ies.get("ft"):
        f.append("802.11r")
    if ies.get("rm_enabled"):
        f.append("802.11k")
    if ies.get("btm"):
        f.append("802.11v")
    if ies.get("mbssid"):
        f.append("MBSSID")
    if ies.get("wmm"):
        f.append("WMM")
    if ies.get("he_6ghz") or band == "6":
        f.append("6 GHz")
    if ies.get("he"):
        f.append("Wi-Fi 6")
    elif ies.get("vht"):
        f.append("Wi-Fi 5")
    elif ies.get("ht"):
        f.append("Wi-Fi 4")
    if ies.get("mbo"):
        f.append("MBO")
    if ies.get("ciphers"):
        f.append("/".join(sorted(ies["ciphers"])))
    return f


def _open_handle():
    neg = wt.DWORD()
    handle = wt.HANDLE()
    rc = wlanapi.WlanOpenHandle(2, None, ctypes.byref(neg), ctypes.byref(handle))
    if rc != ERROR_SUCCESS:
        raise RuntimeError("WlanOpenHandle 失败 rc=%d（无线服务未运行或权限不足）" % rc)
    return handle, neg.value


def _enum_first_iface(handle):
    size = wt.DWORD()
    ptr = ctypes.c_void_p()
    rc = wlanapi.WlanEnumInterfaces(handle, None, ctypes.byref(ptr), ctypes.byref(size))
    if rc != ERROR_SUCCESS:
        raise RuntimeError("WlanEnumInterfaces 失败 rc=%d" % rc)
    try:
        if not ptr.value:
            return None
        lst = ctypes.cast(ptr, ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)).contents
        n = int(lst.dwNumberOfItems)
        if n <= 0:
            return None
        base = ctypes.addressof(lst) + 8
        step = ctypes.sizeof(WLAN_INTERFACE_INFO)
        descs, guids, states = [], [], []
        for i in range(n):
            info = WLAN_INTERFACE_INFO.from_address(base + i * step)
            descs.append(str(info.strInterfaceDescription))
            guids.append(bytes(info.InterfaceGuid))
            states.append(int(info.isState))
        return n, descs, guids, states
    finally:
        wlanapi.WlanFreeMemory(ptr)


def scan_bss(wait_seconds=4.5, trigger=True):
    """返回 BSS 列表（含隐藏 SSID 的空名条目）。

    trigger=True  触发一次 WlanScan 主动扫描，结果最新，但网卡会短暂切信道（约 4-8 秒等待）
    trigger=False 只读系统扫描缓存，毫秒级返回、不影响当前关联，适合漫游监测这类高频场景
    """
    handle, negotiated = _open_handle()
    try:
        info = _enum_first_iface(handle)
        if not info:
            return {"ok": False, "error": "未找到无线接口（可能是有线连接或无线网卡被禁用）", "bss": []}
        count, descs, guids, states = info
        if trigger:
            for g in guids:
                gbuf = ctypes.create_string_buffer(g, 16)
                try:
                    wlanapi.WlanScan(handle, ctypes.cast(gbuf, ctypes.c_void_p), None, None, None)
                except Exception:
                    pass
            time.sleep(max(1.0, min(8.0, wait_seconds)))
        else:
            time.sleep(0.05)
        bss_out = []
        for idx, g in enumerate(guids):
            gbuf = ctypes.create_string_buffer(g, 16)
            pList = ctypes.c_void_p()
            rc = wlanapi.WlanGetNetworkBssList(handle, ctypes.cast(gbuf, ctypes.c_void_p), None,
                                               DOT11_BSS_TYPE_ANY, False, None, ctypes.byref(pList))
            if rc != ERROR_SUCCESS or not pList.value:
                continue
            try:
                lst = ctypes.cast(pList, ctypes.POINTER(WLAN_BSS_LIST)).contents
                total = int(lst.dwNumberOfItems)
                total_size = int(lst.dwTotalSize)
                stride = BSS_ENTRY_SIZE
                data_all = ctypes.string_at(pList.value, total_size) if total_size else b""
                # 驱动返回的计数和 IE 内容都属于外部输入，先约束到实际缓冲区。
                max_entries = max(0, (len(data_all) - 8) // stride)
                total = min(total, max_entries)
                for i in range(total):
                    entry_start = 8 + i * stride
                    eb = data_all[entry_start:entry_start + stride]
                    ssid_len = min(_u32(eb, OFF_SSID_LEN), 32)
                    raw = eb[OFF_SSID:OFF_SSID + ssid_len] if ssid_len else b""
                    ssid = raw.decode("utf-8", "ignore").strip("\x00")
                    bssid = ":".join("%02x" % b for b in eb[OFF_BSSID:OFF_BSSID + 6])
                    ie_rel = _u32(eb, OFF_IE_OFFSET)
                    ie_len = _u32(eb, OFF_IE_SIZE)
                    ie = b""
                    if ie_len and ie_rel >= stride:
                        ie_abs = entry_start + ie_rel   # ulIeOffset 相对本条目起点
                        safe_len = min(ie_len, MAX_IE_BLOB)
                        if ie_abs + safe_len <= len(data_all):
                            ie = data_all[ie_abs:ie_abs + safe_len]
                    ies = _parse_ies(ie)
                    freq = _u32(eb, OFF_FREQ_KHZ)
                    ch = _freq_to_channel(freq)
                    if ch is None and ies["channels"]:
                        ch = ies["channels"][0]
                    q = _u32(eb, OFF_LINK_QUALITY)
                    rssi = _i32(eb, OFF_RSSI) if stride >= OFF_RSSI + 4 else None
                    if rssi is None or not (-100 <= rssi <= -10):
                        rssi = int(round(q / 2.0 - 100))
                    hidden = (ssid_len == 0) or (ssid == "")
                    if hidden and ies.get("ssid_in_ie"):
                        ssid = ies["ssid_in_ie"]
                        hidden = False
                    band = _band_of(ch, freq)
                    width = ies["width_mhz"] or 20
                    bss_out.append({
                        "ssid": ssid, "hidden": hidden, "bssid": bssid,
                        "rssi": rssi, "link_quality": q,
                        "channel": ch, "band": band, "width_mhz": width,
                        "freq_mhz": round(freq / 1000.0, 1) if freq else None,
                        "phy": _phy_from_ies(ies, width),
                        "security": _security_label(ies),
                        "beacon_period": _u16(eb, OFF_BEACON_PERIOD),
                        "capability": _u16(eb, OFF_CAPABILITY),
                        "bss_load": ies["bss_load"],
                        "country": ies["country"],
                        "vendor": sorted(ies["vendor_ouis"])[0] if ies["vendor_ouis"] else "",
                        "cap": {
                            "flags": capability_flags(ies, band),
                            "pmf": ("required" if ies["pmf_required"] else
                                    "capable" if ies["pmf_capable"] else
                                    "none" if ies["rsn"] else "n/a"),
                            "ft": bool(ies["mobility_domain"] or ies["ft"]),
                            "rm": bool(ies["rm_enabled"]), "btm": bool(ies["btm"]),
                            "mbssid": bool(ies["mbssid"]), "wmm": bool(ies["wmm"]),
                            "sae": bool(ies["sae"] or ies["ft_sae"]), "owe": bool(ies["owe"]),
                            "wpa3": bool(ies["sae"] or ies["ft_sae"] or ies["owe"]),
                            "transition": bool((ies["sae"] or ies["ft_sae"]) and ies["rsn_akms"] &
                                               {a for a in ies["rsn_akms"] if a[-2:].lower() in ("02", "04")}),
                            "ciphers": sorted(ies["ciphers"]), "group_cipher": ies["group_cipher"],
                            "vendor_names": _vendor_names(ies["vendor_ouis"]),
                            "dtim_period": ies["dtim_period"], "bss_color": ies["bss_color"],
                            "ext_caps": ies["ext_caps"], "he_6ghz": bool(ies["he_6ghz"]),
                            "beacon_period": _u16(eb, OFF_BEACON_PERIOD),
                            "note": "能力由 Beacon/Probe 信息元素位推断，厂商实现可能有差异；未做关联协商验证",
                        },
                        "interface": descs[idx],
                    })
            finally:
                wlanapi.WlanFreeMemory(pList)
        # 去重（同一 BSSID 多次上报）
        uniq = {}
        for b in bss_out:
            k = b["bssid"]
            if k not in uniq or (b.get("rssi") or -999) > (uniq[k].get("rssi") or -999):
                uniq[k] = b
        return {"ok": True, "interfaces": descs, "bss": list(uniq.values()),
                "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "negotiated_version": negotiated}
    finally:
        try:
            wlanapi.WlanCloseHandle(handle, None)
        except Exception:
            pass


if __name__ == "__main__":
    import json
    r = scan_bss()
    print(json.dumps({k: (v if k != "bss" else v[:6]) for k, v in r.items()}, ensure_ascii=False, indent=1))
    print("total bss", len(r.get("bss", [])))
