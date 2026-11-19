# -*- coding: utf-8 -*-
"""设备型号库生成器：单一数据源，输出 CSV / JSON / 可浏览 HTML。

维护方式：修改下方数据块后重新运行 `python build_catalog.py`，
三个产物会同步更新，不需要手工改三份文件。
"""
import csv
import json
import os
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
DATE = "2026-11-09"
VERSION = "1.1"

FIELDS = [
    "category", "origin", "vendor", "family", "models", "device_role",
    "wifi_gen", "bands", "form_factor", "plane_type", "mgmt_examples",
    "min_version", "adapter_id", "verify_status", "verified_date", "note",
    # v1.1 新增：区分在售 / 存量 / EOL / 历史品牌，并驱动能力降级
    "lifecycle", "capability_tier",
]

# vendor -> (产地, 默认 adapter_id, 默认管理平面)
V = {
    "Cisco": ("国外", "cisco_9800", "C9800 / CW9800"),
    "Cisco Meraki": ("国外", "meraki_dashboard", "Meraki Dashboard"),
    "HPE Aruba": ("国外", "aruba_central", "Aruba Central / AOS"),
    "Fortinet": ("国外", "fortigate_wlc", "FortiGate / FortiManager"),
    "Juniper Mist": ("国外", "mist_cloud", "Mist Cloud"),
    "Ruckus": ("国外", "ruckus_sz", "SmartZone / Ruckus Cloud"),
    "Extreme": ("国外", "extremecloud_iq", "ExtremeCloud IQ"),
    "Ubiquiti UniFi": ("国外", "unifi_network", "UniFi Network"),
    "Arista": ("国外", "arista_cloudvision", "CloudVision"),
    "Cambium": ("国外", "cambium_cnmaestro", "cnMaestro"),
    "华为": ("国内", "huawei_ac", "AirEngine AC / iMaster NCE"),
    "H3C": ("国内", "h3c_wx", "WX 系列 / iMC"),
    "锐捷": ("国内", "ruijie_ws", "RG-WS / WIS"),
    "信锐": ("国内", "sundray_nac", "NAC / iBrain NMC"),
    "TP-LINK 商用": ("国内", "tplink_ac", "TL-AC / 商用云"),
    "中兴": ("国内", "generic_snmp_v2c", "按现网补录"),
    "通用": ("通用", "generic_snmp_v2c", "通用 SNMP v2c"),
    # 已退市 / 被收购品牌：只做存量识别，不假设现代 API 能力
    "Aerohive（历史）": ("国外", "generic_snmp_v2c", "HiveManager Classic / NG（已并入 ExtremeCloud IQ）"),
    "Meru（历史）": ("国外", "generic_snmp_v2c", "Meru System Director（已并入 FortiWLC）"),
    "Symbol/Zebra（历史）": ("国外", "generic_snmp_v2c", "WiNG / RFS 控制器（已并入 Extreme）"),
}

rows = []
FAM = ""
ERA = "modern"          # modern / legacy / historic，决定 lifecycle 默认值


def fam(x):
    global FAM
    FAM = x


def set_era(x):
    global ERA
    ERA = x


def rec(v, cat, model, role, gen, bands, form, plane, minv, status, note,
        mgmt=None, adapter=None, era=None):
    origin, default_adapter, default_mgmt = V[v]
    cur = era or ERA
    if status == "预留":
        life = "预留"
    elif cur == "legacy":
        life = "EOL 已停产"
    elif cur == "historic":
        life = "历史品牌"
    elif "存量" in FAM:
        life = "存量在用"
    else:
        life = "主流在售"
    if life in ("EOL 已停产", "历史品牌", "预留"):
        tier = "C-降级"
    elif gen in ("Wi-Fi 7", "Wi-Fi 6E"):
        tier = "A-完整"
    else:
        tier = "B-受限"
    rows.append({
        "category": cat, "origin": origin, "vendor": v, "family": FAM,
        "models": model, "device_role": role, "wifi_gen": gen, "bands": bands,
        "form_factor": form, "plane_type": plane,
        "mgmt_examples": default_mgmt if mgmt is None else mgmt,
        "min_version": minv, "adapter_id": adapter or default_adapter,
        "verify_status": status, "verified_date": DATE, "note": note,
        "lifecycle": life, "capability_tier": tier,
    })


def ap(v, model, gen="Wi-Fi 6", bands="2.4/5", form="室内吸顶",
       status="待核对", note="-", minv="-", mgmt=None, adapter=None):
    rec(v, "AP", model, "AP", gen, bands, form, "-", minv, status, note,
        mgmt, adapter)


def ctl(v, model, plane="独立硬件AC", status="待核对", note="-",
        minv="-", gen="混合", mgmt=None, adapter=None):
    if "-CL" in model or "vSZ" in model or "VWS" in model:
        plane = "虚拟嵌入式"
    rec(v, "Controller", model, "控制器", gen, "-", "-", plane, minv, status, note,
        mgmt, adapter)


def cloud(v, model, status="待核对", note="-", mgmt=None, adapter=None):
    rec(v, "ManagementPlane", model, "云管理", "混合", "-", "-", "云管理", "-",
        status, note, mgmt, adapter)


# ---------------- 国外：Cisco ----------------
fam("Catalyst 9100 Wi-Fi 6")
for m in "C9105AXI C9105AXW C9115AXI C9115AXE C9117AXI C9120AXI C9120AXE C9120AXP C9130AXI C9130AXE C9136I".split():
    ap("Cisco", m, status="已核实", note="Cisco 9800 17.18.3 官方兼容矩阵")
fam("Catalyst 916x Wi-Fi 6E")
for m in "C9162I C9164I C9166I C9166D1".split():
    ap("Cisco", m, gen="Wi-Fi 6E", bands="2.4/5/6", status="已核实", note="Cisco 9800 17.18.3 官方兼容矩阵")
fam("CW917x Wi-Fi 7")
for m in "CW9171I CW9172I CW9172H CW9174I CW9174E CW9176I CW9176D1 CW9178I CW9179F".split():
    ap("Cisco", m, gen="Wi-Fi 7", bands="2.4/5/6", status="已核实",
       minv="IOS XE 17.15.2", note="17.18.3 兼容矩阵; 官方推荐 17.18.4a")
fam("Cisco 室外与工业")
for m in "1542I 1542D 1562I 1562D 1562E C9124AXI C9124AXD C9124AXE C9163E IW6300 IW9167I IW9167E IW9165D IW9165E 1800s ISR-AP1100AC ISR-AP1101AC ISR-AP1101AX".split():
    ap("Cisco", m, form="室外/工业", status="已核实", note="Cisco 9800 17.18.3 官方兼容矩阵")
fam("Aironet 存量 Wi-Fi 5")
for m in "1815I 1815W 1815M 1815T 1830I 1840I 1852I 1852E 1800i 2800I 2800E 3800I 3800E 3800P 4800I".split():
    ap("Cisco", m, gen="Wi-Fi 5", status="已核实", note="Cisco 9800 17.18.3 官方兼容矩阵")
fam("Catalyst 9800 控制器")
for m in "C9800-L C9800-40 C9800-80 C9800-CL CW9800L CW9800M CW9800H1 CW9800H2".split():
    ctl("Cisco", m, status="已核实", note="Cisco IOS XE")
ctl("Cisco", "EWC on AP", plane="虚拟嵌入式", status="已核实")
ctl("Cisco", "EWC on Catalyst switch", plane="虚拟嵌入式", status="已核实")
cloud("Cisco", "Catalyst Center", status="已核实")
cloud("Cisco", "Cisco Spaces", status="已核实")
fam("Meraki MR 系列")
for m in "MR28 MR36 MR44 MR46 MR46E MR53E MR56 MR57 MR76 MR86".split():
    ap("Cisco Meraki", m, status="待核对", note="按 Meraki 最新产品目录核对")
cloud("Cisco Meraki", "Meraki Dashboard", status="已核实")

# ---------------- 国外：HPE Aruba ----------------
fam("Aruba AP-5xx Wi-Fi 6")
for m in "AP-503H AP-505 AP-505H AP-515 AP-535 AP-555 AP-575 AP-577".split():
    ap("HPE Aruba", m, status="待核对")
fam("Aruba AP-6xx Wi-Fi 6E")
for m in "AP-615 AP-634 AP-635 AP-654 AP-655".split():
    ap("HPE Aruba", m, gen="Wi-Fi 6E", bands="2.4/5/6", status="已核实")
fam("Aruba AP-7xx Wi-Fi 7")
for m in "AP-730 AP-740 AP-750 AP-755".split():
    ap("HPE Aruba", m, gen="Wi-Fi 7", bands="2.4/5/6", status="已核实")
fam("Aruba Mobility Controller")
for m in "7005 7010 7024 7030 7205 7210 7220 7240XM 7280 9240".split():
    ctl("HPE Aruba", m, status="待核对")
ctl("HPE Aruba", "Mobility Conductor", status="待核对")
cloud("HPE Aruba", "Aruba Central (AOS 8)", status="已核实")
cloud("HPE Aruba", "Aruba Central (AOS 10)", status="已核实")
cloud("HPE Aruba", "ClearPass", status="已核实", note="认证联动 非控制器")

# ---------------- 国外：Fortinet ----------------
fam("FortiAP F 系列 Wi-Fi 6")
for m in "FAP-231F FAP-233F FAP-234F FAP-23JF FAP-431F FAP-433F FAP-432F FAP-831F".split():
    ap("Fortinet", m, status="已核实")
fam("FortiAP G 系列 Wi-Fi 6E")
for m in "FAP-231G FAP-233G FAP-431G FAP-433G FAP-234G FAP-432G FAP-432FR".split():
    ap("Fortinet", m, gen="Wi-Fi 6E", bands="2.4/5/6", status="已核实")
fam("FortiAP K 系列 Wi-Fi 7")
for m in "FAP-231K FAP-241K FAP-243K FAP-441K FAP-443K".split():
    ap("Fortinet", m, gen="Wi-Fi 7", bands="2.4/5/6", status="已核实")
fam("FortiGate 内置控制器与集中管理")
ctl("Fortinet", "FortiGate 内置 WLAN 控制器", plane="网关内置", status="已核实", note="无独立 WLC")
ctl("Fortinet", "FortiGate 400F 内置 WLAN 控制器", plane="网关内置", status="已核实", note="现网已有设备")
ctl("Fortinet", "FortiManager", plane="网关内置", status="已核实", note="多站点集中管理")
cloud("Fortinet", "FortiLAN Cloud", status="已核实")
cloud("Fortinet", "FortiWLM", status="已核实")
cloud("Fortinet", "FortiPresence", status="待核对", note="位置分析")

# ---------------- 国外：Juniper Mist ----------------
fam("Mist AP Wi-Fi 5 / 6")
for m in "AP12 AP21 AP32 AP33 AP41 AP43 AP61 AP63".split():
    ap("Juniper Mist", m, status="待核对")
ap("Juniper Mist", "AP45", gen="Wi-Fi 6E", bands="2.4/5/6", status="已核实")
fam("Mist AP Wi-Fi 7")
for m in "AP47 AP67".split():
    ap("Juniper Mist", m, gen="Wi-Fi 7", bands="2.4/5/6", status="已核实")
ap("Juniper Mist", "BT11", gen="-", bands="-", form="BLE 传感器", status="待核对")
fam("Mist 管理平面")
ctl("Juniper Mist", "Mist Edge", plane="虚拟嵌入式", status="已核实", note="本地数据面")
cloud("Juniper Mist", "Mist Cloud / Marvis AI", status="已核实")

# ---------------- 国外：Ruckus ----------------
fam("Ruckus R 系列 Wi-Fi 5 / 6")
for m in "R310 R320 R350 R510 R550 R610 R650 R710 R720 R730 R750 R850".split():
    ap("Ruckus", m, status="待核对")
ap("Ruckus", "R760", gen="Wi-Fi 6E", bands="2.4/5/6", status="已核实")
fam("Ruckus Wi-Fi 7")
for m in "R770 T870".split():
    ap("Ruckus", m, gen="Wi-Fi 7", bands="2.4/5/6", status="已核实")
fam("Ruckus 室外 / 面板 / 工业")
for m in "T310 T610 T710 T750 T750SE H320 H510 H550 E510 M510 P300".split():
    ap("Ruckus", m, form="室外/面板", status="待核对")
fam("Ruckus 控制器与云管")
for m in "ZoneDirector 1200|SZ100|SZ144|SZ300|vSZ-H".split("|"):
    ctl("Ruckus", m, status="待核对")
cloud("Ruckus", "Ruckus Cloud", status="待核对")
cloud("Ruckus", "Ruckus One", status="待核对")

# ---------------- 国外：Extreme ----------------
fam("Extreme Wi-Fi 6 / 6E AP")
for m in "AP305C AP305CX AP310i AP410C AP460C AP460S AP3912i AP3916ic AP3965i".split():
    ap("Extreme", m, status="待核对")
for m in "AP5010 AP7632 AP8432 AP8533".split():
    ap("Extreme", m, status="待核对")
ap("Extreme", "AP5050", gen="Wi-Fi 7", bands="2.4/5/6", status="已核实")
fam("Extreme 管理平面")
cloud("Extreme", "ExtremeCloud IQ", status="已核实")
ctl("Extreme", "ExtremeCloud IQ Controller", status="待核对")
cloud("Extreme", "ExtremeCloud IQ Site Engine", status="待核对")

# ---------------- 国外：Ubiquiti UniFi ----------------
fam("UniFi U7 / E7 Wi-Fi 7")
for m in "U7 Pro XGS|U7 Pro XG|U7 Pro Max|U7 Pro|U7 Long-Range|U7 Lite|U7 Pro XG Wall|U7 Pro Wall|U7 In-Wall|U7 Pro Outdoor|U7 Outdoor|U7 Industrial|U7 Mesh|E7|E7 Campus|E7 Audience".split("|"):
    ap("Ubiquiti UniFi", m, gen="Wi-Fi 7", bands="2.4/5/6", status="已核实", note="Ubiquiti 官网旗舰 AP 列表")
fam("UniFi U6 / AC 系列")
for m in "U6 Enterprise|U6 Pro|U6 Long-Range|U6+|U6 Lite|U6 Mesh|U6 Mesh Pro|U6 Extender|U6 Enterprise In-Wall|U6 In-Wall|AC Mesh|AC Mesh Pro|Swiss Army Knife Ultra|WiFi BaseStation XG".split("|"):
    ap("Ubiquiti UniFi", m, status="已核实", note="U6 Enterprise 为 Wi-Fi 6E")
fam("UniFi 管理平面")
ctl("Ubiquiti UniFi", "UniFi Network Server (自托管)", plane="虚拟嵌入式", status="已核实")
cloud("Ubiquiti UniFi", "UniFi Site Manager", status="已核实")
for m in "UDM|UDM Pro|UDM SE|Cloud Key Gen2|UCG Ultra".split("|"):
    ctl("Ubiquiti UniFi", m, plane="网关内置", status="待核对")

# ---------------- 国外：Arista / Cambium ----------------
fam("Arista Campus AP")
for m in "C-260 C-360 C-460".split():
    ap("Arista", m, gen="Wi-Fi 7", bands="2.4/5/6", status="已核实" if m in ("C-360", "C-460") else "待核对")
cloud("Arista", "CloudVision", status="已核实")
fam("Cambium XV 系列")
for m in "XV2-2 XV2-22H XV2-23T XV3-8".split():
    ap("Cambium", m, status="待核对")
cloud("Cambium", "cnMaestro", status="待核对")

# ---------------- 国内：华为 ----------------
fam("AirEngine Wi-Fi 6")
for m in "5760-10 5760-22W 5761-11 5761-12W 5761S-10W 5762-12 5762-12SW 6760-X1 6761-21T 6760-51EI".split():
    ap("华为", m, status="待核对", note="按华为最新产品目录核对 SKU")
fam("AirEngine Wi-Fi 7")
for m in "5773-22P 6776-56TP 8776-X6THP".split():
    ap("华为", m, gen="Wi-Fi 7", bands="2.4/5/6", status="已核实")
fam("AirEngine 室外与面板")
for m in "AirEngine 室外系列(待补录)|AirEngine 面板系列(待补录)".split("|"):
    ap("华为", m, form="室外/面板", status="预留", note="按现网清单补录")
fam("AirEngine AC 控制器")
for m in "AirEngine 9703-H|AirEngine 9703-H-T|AirEngine 9503-S|AirEngine 9701-L-T|AC6508|AC6005|AC6605|AC6805".split("|"):
    ctl("华为", m, status="已核实" if m in ("AirEngine 9703-H", "AirEngine 9503-S", "AirEngine 9701-L-T", "AC6508") else "待核对")
cloud("华为", "iMaster NCE-Campus", status="已核实")
cloud("华为", "CloudCampus", status="待核对")
cloud("华为", "eSight", status="待核对")

# ---------------- 国内：H3C ----------------
fam("H3C WA 系列 Wi-Fi 6")
for m in "WA6320 WA6338 WA6628X WA6528 WA5530 WA5320 WA5320E WA4320".split():
    ap("H3C", m, status="待核对", note="按 H3C 最新产品目录核对 SKU")
fam("H3C WA7xxx Wi-Fi 7")
ap("H3C", "WA7539", gen="Wi-Fi 7", bands="2.4/5/6", status="已核实")
for m in "WA7338 WA7538 WA7220".split():
    ap("H3C", m, gen="Wi-Fi 7", bands="2.4/5/6", status="待核对")
ap("H3C", "UAP612MG-AX30", form="面板", status="已核实")
fam("H3C WX 无线控制器")
for m in "WX3508H WX3510H WX3520H WX3540H WX3500H-LI".split():
    ctl("H3C", m, status="已核实", note="官网标注可管 Wi-Fi 7 AP 但社区资料有分歧 需核对")
for m in "WX3510X WX3520X WX3540X WX5500X WX6500X WX3024E WX3010E".split():
    ctl("H3C", m, status="待核对", note="新一代 / 存量 AC")
cloud("H3C", "iMC", status="已核实")
cloud("H3C", "Cloudnet", status="已核实")
cloud("H3C", "AD-Campus", status="待核对")
cloud("H3C", "U-Center", status="待核对")

# ---------------- 国内：锐捷 ----------------
fam("锐捷 Wi-Fi 7 全场景")
for m in "RG-MAP920-SF-M|RG-AP9861-R|RG-AP9850-R|RG-AP9751-R|RG-AP9520-RDX|RG-AP9220|RG-AP9220(V2)|RG-AP690D-A|RG-AP690-A|RG-AP1920|RG-AP1920-X|RG-AP1920(V2)|RG-AP194-A|RG-AP900-A|RG-AP950-AR-RU|RG-AP690-O-RU|RG-MAP920-SF-M(V3)|RG-MAP920-SF-U|RG-MAP920-SF-MXT|RG-AP690-A(V2)|RG-AP690D-A(V2)".split("|"):
    ap("锐捷", m, gen="Wi-Fi 7", bands="2.4/5/6", status="已核实", note="锐捷官网无线产品列表")
fam("锐捷 Wi-Fi 6 / 存量 AP")
for m in "RG-AP840-AR|RG-AP800|RG-AP820-A(V3)|RG-AP720-I|RG-AP740-I|RG-AP730-I|RG-AP730-L|RG-AP730(TR)|RG-AP720-L|RG-AP720-A|RG-AP520-I(SR)|RG-AP520(DA)|RG-AP520(BT)|RG-AP330-I|RG-AP3220|RG-AP3220-P|RG-AP180-A(V3)|RG-AP180-L-A(V3)|RG-AP110-A".split("|"):
    ap("锐捷", m, gen="Wi-Fi 6", status="已核实", note="锐捷官网无线产品列表")
fam("锐捷 RG-WS 无线控制器")
for m in "RG-WS7005-A|RG-WS7005-A(V2)|RG-WS7216-E|RG-WS7880|RG-WS7216-S|RG-WS7208-A|RG-WS7204-A|RG-WS6816|RG-WS6512|RG-WS6108|RG-WS6008|RG-WS2000-A|RG-WS7816C|RG-WS7110|RG-WS7110C|RG-WS6816C|RG-WS6812|RG-WIE7202|RG-VWS|RG-WS7228-A|RG-WS7224-A".split("|"):
    ctl("锐捷", m, status="已核实", note="锐捷官网无线控制器列表")
for m in "RG-MXR-2|RG-MX-8|RG-M8600E-WS-ED|RG-M8600-WS|RG-M18000-WS-ED|RG-M7000-WS|RG-M12000-WS".split("|"):
    ctl("锐捷", m, plane="独立硬件AC", status="已核实", note="无线控制模块 / 智能无线交换机")
cloud("锐捷", "RG-WIS", status="已核实")
cloud("锐捷", "WIS+EMB 云地一体", status="已核实")

# ---------------- 国内：信锐 ----------------
fam("信锐 Turbo Sense Wi-Fi 7")
for m in "NAP-5737(SR) NAP-3927(SR) NAP-3827(SR) NAP-3827PF(SR)".split():
    ap("信锐", m, gen="Wi-Fi 7", bands="2.4/5/6", status="已核实")
fam("信锐 NAP Wi-Fi 6 / 存量")
for m in "NAP-1600 NAP-2800 NAP-3600 NAP-4650 NAP-1700 NAP-3600(MU) NAP-3700 NAP-5600 NAP-8000 NAP-8000(L) NAP-8100 NAP-8100(L) NAP-2800-P NAP-3600-P NAP-3600-P(MU) NAP-1720-LTE NAP-3620 NAP-3620R3 NAP-3680 NAP-4100V".split():
    ap("信锐", m, status="已核实", note="信锐产品资料")
fam("信锐锐灵 XAP")
for m in "XAP-6220-E XAP-6210-E XAP-5520-E".split():
    ap("信锐", m, status="已核实")
fam("信锐 NAC 控制器")
for m in "NAC-6100 NAC-6200 NAC-6300 NAC-6600 NAC-7100 NAC-7200 NAC-7300 NAC-7600 HG-2500-P 超体XAC SAC-1000 CAC-2000 NAC-6300H NAC6100-IOT NAC6200-IOT".split():
    ctl("信锐", m, status="已核实")
cloud("信锐", "iBrain NMC", status="已核实")
cloud("信锐", "信锐 IOT 物联平台", status="待核对")

# ---------------- 国内：TP-LINK 商用 / 预留 ----------------
fam("TP-LINK 商用 AP")
for m in "TL-AP1200GI-PoE TL-AP1200C-PoE TL-AP1900DG TL-AP450C-PoE TL-AP302C-PoE TL-AP300C-PoE TL-AP1750C-PoE".split():
    ap("TP-LINK 商用", m, status="待核对", note="按商用最新目录核对")
fam("TP-LINK 商用 AC")
for m in "TL-AC200 TL-AC500 TL-AC1000 TL-AC10000".split():
    ctl("TP-LINK 商用", m, status="已核实")
cloud("TP-LINK 商用", "TP-LINK 商用云平台", status="待核对")
fam("预留厂商位")
ap("中兴", "待补录 (AP)", status="预留", note="按现网清单补录")
ctl("中兴", "待补录 (AC)", status="预留")
fam("通用降级")
rec("通用", "Fallback", "未收录型号(通用档案)", "降级档案", "待核对", "-", "-", "-", "-", "预留",
    "未知型号降级 不阻塞使用", mgmt="通用 SNMP v2c + 手工射频参数")

# ---------------- 老旧 / EOL / 历史品牌（v1.1 新增） ----------------
# 老型号与现代型号共用同一 schema，靠 lifecycle / capability_tier 区分。
# adapter_id 指向旧管理平面；min_version 记录该老 AP 可用的最后一版控制器固件。
def legacy_ap(v, models, gen, bands, note, minv="-", adapter=None, mgmt=None,
              form="室内吸顶", status="已核实", era="legacy"):
    set_era(era)
    for m in models.split("|"):
        ap(v, m, gen=gen, bands=bands, form=form, status=status,
           note=note, minv=minv, mgmt=mgmt, adapter=adapter)


def legacy_ctl(v, models, note, minv="-", adapter=None, mgmt=None,
               plane="独立硬件AC", status="已核实", era="legacy"):
    set_era(era)
    for m in models.split("|"):
        ctl(v, m, plane=plane, status=status, note=note,
            minv=minv, mgmt=mgmt, adapter=adapter)


set_era("legacy")

# Cisco Aironet：AireOS 体系，9800 只收 Wave1/Wave2 部分型号
fam("Aironet 802.11a/b/g 早期 (EOL)")
legacy_ap("Cisco", "AIR-AP340|AIR-AP350|AIR-AP1100|AIR-AP1121G|AIR-AP1230AG|AIR-AP1310|AIR-AP1410|AIR-BR1310G",
          "802.11a/b/g", "2.4/5", "Aironet 最早一代自主/轻量 AP；按现网铭牌核对",
          minv="AireOS 7.0 之前", adapter="autonomous_ap_snmp_v2c",
          mgmt="自主 AP 或 4400 系列 WLC", status="待核对")
legacy_ap("Cisco", "1130AG|1140|1240AG|1250|1260|1040|700i|700W|702i|702W|AP801|AP802|1600|2600|3500i|3500e|3600i|3600e",
          "Wi-Fi 4", "2.4/5", "Cisco FlexConnect / AireOS 官方矩阵列名；AireOS 8.5 是 1600/2600/3500/3600 末版；1240AG 2018-07-31 终止支持；9800 不支持",
          minv="AireOS 8.5（末版）", adapter="aireos_snmp_v2c",
          mgmt="AireOS WLC 2504/5508/8510/WiSM2", status="已核实")
legacy_ap("Cisco", "1520|1530|1550|1552|1570|1572|703W|1810",
          "Wi-Fi 4", "2.4/5", "Aironet 室外 / Mesh / 面板老品；1552 128MB 版止于 AireOS 8.5；1570 为 Wave1 11ac",
          minv="AireOS 8.5", adapter="aireos_snmp_v2c",
          mgmt="AireOS WLC 2504/5508/8510", form="室外/面板", status="已核实")
legacy_ap("Cisco", "1700|2700|3700|3700P|IW3700",
          "Wi-Fi 5", "2.4/5", "官方 FN74109 明确 Wave1 11ac AP 可注册 9800 WLC；其余 11n 老 AP 不可",
          minv="IOS XE 17.3 / AireOS 8.5", adapter="aireos_snmp_v2c",
          mgmt="Catalyst 9800 或 AireOS WLC", form="室外/工业", status="已核实")
fam("AireOS / 融合接入 WLC (EOL)")
legacy_ctl("Cisco", "WLC 2006|WLC 2106|WLC 2112|WLC 2125|WLC 4402|WLC 4404|WLC 2504|WLC 5508|WLC 3504|WLC 5520|WLC 7510|WLC 8510|WLC 8540|WiSM|WiSM2|Flex 7500|Flex 8500",
           "Cisco 官方 AireOS 版本建议：8.5 是 2504/5508/7510/8510/WiSM2 最后一版；8.10 为 3504/5520/8540 末版；8.9/8.8/8.7/8.6 已淘汰",
           minv="AireOS 8.5 / 8.10", adapter="aireos_snmp_v2c",
           mgmt="AireOS WLC SNMP v2c + 私有 MIB", status="已核实")
legacy_ctl("Cisco", "vWLC|Catalyst 5760 WLC|Catalyst 3850 WLC|Catalyst 3650 WLC|Catalyst 3750G 集成 WLC|NM-AIR-WLC6|NM-AIR-WLC8|ISR WLC 模块|Mobility Express",
           "虚拟 / 交换机内置 / AP 内置控制器旧架构；不支持与 9800 的 IRCM",
           minv="AireOS 8.5 / IOS XE 3.x", adapter="aireos_snmp_v2c",
           mgmt="交换机或 AP 内置 WLC", plane="虚拟嵌入式", status="待核对")

# Meraki MR 早期（云管理，EOL 后固件冻结）
fam("Meraki MR 早期 802.11n (EOL)")
legacy_ap("Cisco Meraki", "MR11|MR12|MR14|MR16|MR18|MR24|MR26|MR58|MR62|MR66",
          "Wi-Fi 4", "2.4/5", "Meraki 官方 EOL 清单：MR16/MR24 止于 2021-05-31，MR18 2024-03-31，MR12 2022-10-24；固件上限 MR 26.x",
          minv="MR 26.x（上限）", adapter="meraki_dashboard_legacy",
          mgmt="Meraki Dashboard（云）", status="已核实")
fam("Meraki MR 早期 802.11ac (EOL)")
legacy_ap("Cisco Meraki", "MR20|MR30H|MR32|MR33|MR34|MR42|MR42E|MR45|MR52|MR53|MR55|MR72|MR74|MR84",
          "Wi-Fi 5", "2.4/5", "Meraki 官方 EOL 清单：MR34 止于 2023-10-31，MR42/52/53/84 止于 2026-07-21；EOL 后 Dashboard 不保证长期可连",
          minv="MR 26.x / 30.x（上限）", adapter="meraki_dashboard_legacy",
          mgmt="Meraki Dashboard（云）", status="已核实")
legacy_ap("Cisco Meraki", "MX60W|MX64W|MX65W|MX67W|MX68W|Z1",
          "Wi-Fi 4", "2.4/5", "带无线的安全网关 / 远程办公网关（内置 AP）；按现网铭牌核对",
          minv="MX 14.x / 18.x", adapter="meraki_dashboard_legacy",
          mgmt="Meraki Dashboard（云）", form="网关内置无线", status="待核对")

# HPE Aruba 老 AP 与 AOS 6.x 控制器
fam("Aruba AP 早期 802.11a/b/g / Wi-Fi 4 (EOL)")
legacy_ap("HPE Aruba", "AP-60|AP-61|AP-65|AP-68|AP-68P|AP-70|AP-85|AP-92|AP-93|AP-104|AP-105|AP-114|AP-115|AP-124|AP-125|AP-134|AP-135|AP-175|AP-103|AP-103H|AP-203R|AP-203RP|RAP-155|RAP-3WN|RAP-5WN",
          "Wi-Fi 4", "2.4/5", "Aruba 官方 EOL / AOS 支持矩阵：AP-92/93 与 AP-68 最高 AOS 8.1，AP-124/125 最高 6.4；Campus AP 与 Instant AP 两种形态",
          minv="ArubaOS 6.4 / 8.1（上限）", adapter="aruba_aos6_snmp_v2c",
          mgmt="Aruba 600/3200/6000/M3 控制器 或 Instant", status="已核实")
fam("Aruba AP 2xx / 3xx 存量 (Wi-Fi 5)")
legacy_ap("HPE Aruba", "AP-205|AP-207|AP-215|AP-225|AP-228|AP-275|AP-277|AP-303|AP-303H|AP-304|AP-305|AP-314|AP-315|AP-325|AP-335|AP-345|AP-365|AP-367|AP-375|AP-377|AP-387",
          "Wi-Fi 5", "2.4/5", "Aruba 官方 AOS 支持矩阵：2xx 系列最低 6.3/6.4，3xx 系列最低 6.5/8.0；AOS 8.x 之后逐步停止支持",
          minv="ArubaOS 6.5 / 8.x", adapter="aruba_aos6_snmp_v2c",
          mgmt="Aruba 72xx / 70xx 控制器 或 Instant", status="已核实")
fam("Aruba 早期控制器与网管 (EOL)")
legacy_ctl("HPE Aruba", "600|620|650|651|800|2000|3000|3200|3400|3600|6000|M3|7008|7240",
           "Aruba 官方 AOS 支持矩阵老控制器；600/620/650/3200 等最高 AOS 6.4.x",
           minv="ArubaOS 6.4.x（上限）", adapter="aruba_aos6_snmp_v2c",
           mgmt="Aruba Mobility Controller AOS 6.x", status="已核实")
legacy_ctl("HPE Aruba", "Aruba Instant Virtual Controller|AirWave|Aruba Activate",
           "Instant 虚拟控制器 / AirWave 网管 / Activate 激活服务（旧管理平面）",
           minv="AOS 6.x / AirWave 8.x", adapter="aruba_aos6_snmp_v2c",
           mgmt="Instant / AirWave", plane="虚拟嵌入式", status="待核对")

# Fortinet FortiAP B/C/D/E 系列（FortiGate 内置控制器，版本强耦合）
fam("FortiAP B / C 早期 802.11n (EOL)")
legacy_ap("Fortinet", "FAP-220A|FAP-220B|FAP-221B|FAP-222B|FAP-223B|FAP-320B|FAP-210B|FAP-112B|FAP-11C|FAP-14C|FAP-28C|FAP-25D|FAP-21D|FAP-24D|FAP-112D|FAP-221B-A-G",
          "Wi-Fi 4", "2.4/5", "Fortinet 官方 EOL 公告（经 eol.network 汇总）：220B 2018-10-06、320B 2022-04-25 终止支持",
          minv="FortiOS 5.x / 6.0", adapter="fortigate_wlc_legacy",
          mgmt="FortiGate 内置 WLAN 控制器（旧 FortiOS）", status="已核实")
fam("FortiAP C / D / E 系列 (EOL)")
legacy_ap("Fortinet", "FAP-221C|FAP-222C|FAP-223C|FAP-320C|FAP-321C|FAP-224D|FAP-S221E|FAP-S223E|FAP-S321C|FAP-S322C|FAP-S323C|FAP-S421E|FAP-S422E|FAP-S423E|FAP-C220C|FAP-C225C|FAP-C23JD|FAP-C24JE|FAP-U221EV|FAP-221E|FAP-222E|FAP-223E|FAP-224E|FAP-231E|FAP-321E|FAP-421E|FAP-423E|FAP-S311C|FAP-S313C",
          "Wi-Fi 5", "2.4/5", "Fortinet 官方 EOL / 兼容矩阵：321C 固件上限 6.0.6，FortiOS 7.4+ 不再支持；FortiOS 8.0.0 起不再支持 C 系列、U/W2 E 系列（仅 221E/223E/421E/423E 保留）",
          minv="FortiOS ≤ 7.2（部分 ≤ 6.0）", adapter="fortigate_wlc_legacy",
          mgmt="FortiGate 内置 WLAN 控制器", status="已核实")
fam("Fortinet 旧无线管理平面")
legacy_ctl("Fortinet", "FortiWLC (Meru 血统)|FortiGate WiFi Controller (FortiOS 5.x/6.x)",
           "FortiWLC 独立控制器 / 旧版 FortiGate 内置控制器；现网 400F 属后者的现代版本",
           minv="FortiOS 6.x / FortiWLC 8.x", adapter="fortigate_wlc_legacy",
           mgmt="FortiGate / FortiWLC", plane="网关内置", status="待核对")
legacy_ctl("Fortinet", "FortiCloud (旧版)|FortiWLM 旧版",
           "旧版云管 / 无线网管组件；内网离线场景需降级",
           minv="-", adapter="fortigate_wlc_legacy",
           mgmt="FortiCloud / FortiWLM", plane="虚拟嵌入式", status="待核对")

# Ruckus ZoneFlex 全系列老品
fam("ZoneFlex 802.11g / Wi-Fi 4 (EOL)")
legacy_ap("Ruckus", "ZF2741|ZF2942|ZF7025|ZF7055|ZF7321|ZF7321-U|ZF7341|ZF7343|ZF7351|ZF7352|ZF7363|ZF7372|ZF7372-E|ZF7441|ZF7942|ZF7962|ZF7982|ZF7761-CM|ZF7762|ZF7762-S|ZF7762-T|ZF7762-AC|ZF7762-S-AC|ZF7781-CM|ZF7782|ZF7782-S|ZF8800|H500",
          "Wi-Fi 4", "2.4/5", "Ruckus ZoneDirector 9.7/9.8 官方发行说明与 EoS 表：9.8.3.0.58 是 7962/7762/7025 最后推荐版；9.9 后不支持 7962/7762，9.10 后不支持 7025",
          minv="ZoneDirector 9.8.3.0.58（上限）", adapter="ruckus_zonedirector",
          mgmt="ZoneDirector 1100/3000/5000", form="室内/室外/面板", status="已核实")
fam("ZoneFlex Wi-Fi 5 早期 (EOL)")
legacy_ap("Ruckus", "R300|R500|R600|R700|T300|T301|C500",
          "Wi-Fi 5", "2.4/5", "Ruckus 官方 EoS 表：R300/H500 2022-11-01、R700 2023-02-01、R500/R600/T300/T301 2024-04-30 终止支持",
          minv="ZoneDirector 9.8 / 9.9 / 9.10", adapter="ruckus_zonedirector",
          mgmt="ZoneDirector 1100/3000/5000 或 SmartZone", status="已核实")
fam("Ruckus 早期控制器 (EOL)")
legacy_ctl("Ruckus", "ZoneDirector 1000|ZoneDirector 1100|ZoneDirector 3000|ZoneDirector 5000|SCG200|SZ104|SZ124|FlexMaster",
           "Ruckus 官方 EoS 表：ZD1100 2020-06-30、ZD5000 2021-12-31、ZD3000 2024-01-01 终止支持；ZD1100 最高 9.10，无 legacy 兼容模式",
           minv="ZoneDirector 9.10 / SZ 3.x（上限）", adapter="ruckus_zonedirector",
           mgmt="ZoneDirector / SCG / FlexMaster", status="已核实")

# Extreme / Aerohive / Symbol-Zebra / Meru 历史产品线
fam("Extreme IdentiFi 存量 AP")
legacy_ap("Extreme", "AP3605|AP3610|AP3620|AP3630|AP3710|AP3715|AP3720|AP3725|AP3825i|AP3825e|AP3835i|AP3865e",
          "Wi-Fi 4", "2.4/5", "Extreme IdentiFi（原 Enterasys）存量 AP；按现网铭牌核对",
          minv="IdentiFi 9.x / ExtremeCloud IQ Controller", adapter="extreme_wing_snmp_v2c",
          mgmt="ExtremeCloud IQ Controller / IdentiFi", status="待核对")
fam("Aerohive 历史产品线（2019 被 Extreme 收购）")
legacy_ap("Aerohive（历史）", "HiveAP 20|HiveAP 28|HiveAP 320|HiveAP 330|AP110|AP120|AP121|AP130|AP141|AP230|AP245X|AP250|AP330|AP340|AP350|AP370|AP380|AP390|AP1130|BR100|BR200",
          "Wi-Fi 4", "2.4/5", "Aerohive HiveOS 产品线；HiveManager 已并入 ExtremeCloud IQ；按现网铭牌核对",
          minv="HiveOS 6.x / HiveManager Classic", adapter="aerohive_hivemanager",
          mgmt="HiveManager Classic / NG", era="historic", status="待核对")
legacy_ctl("Aerohive（历史）", "HiveManager Classic|HiveManager NG|HiveManager Online|HiveOS 虚拟控制器",
           "Aerohive 旧管理平面，收购后迁移至 ExtremeCloud IQ",
           minv="HiveManager 6.x / 8.x", adapter="aerohive_hivemanager",
           mgmt="HiveManager", plane="虚拟嵌入式", era="historic", status="待核对")
fam("Symbol / Motorola / Zebra WiNG 历史产品线（无线业务售予 Extreme）")
legacy_ap("Symbol/Zebra（历史）", "AP-650|AP-6511|AP-6521|AP-6522|AP-6532|AP-7131|AP-7131N|AP-7161|AP-7502|AP-7522|AP-7532|AP-7562|AP-7602|AP-7622|AP-7662|AP-8132|AP-8232",
          "Wi-Fi 4", "2.4/5", "Motorola / Zebra WiNG 产品线，后归 Extreme；按现网铭牌核对",
          minv="WiNG 5.x", adapter="extreme_wing_snmp_v2c",
          mgmt="RFS / NX / VX 控制器（WiNG）", era="historic", status="待核对")
legacy_ctl("Symbol/Zebra（历史）", "RFS4000|RFS6000|RFS7000|NX 9000|VX 9000|WiNG 虚拟控制器",
           "WiNG / RFS 无线交换机与虚拟控制器",
           minv="WiNG 5.x", adapter="extreme_wing_snmp_v2c",
           mgmt="WiNG 控制器", era="historic", status="待核对")
fam("Meru 历史产品线（2015 被 Fortinet 收购）")
legacy_ap("Meru（历史）", "AP200|AP300|AP320i|AP320e|AP822i|AP822e|AP832i|AP832e|AP1010|AP1020|AP1100",
          "Wi-Fi 4", "2.4/5", "Meru System Director 体系，后并入 FortiWLC / FortiGate；按现网铭牌核对",
          minv="System Director 7.x", adapter="meru_systemdirector",
          mgmt="Meru System Director / FortiWLC", era="historic", status="待核对")
legacy_ctl("Meru（历史）", "MC1500|MC3000|MC4200|MC5000|MC6000|System Director 虚拟版",
           "Meru 控制器 / System Director 虚拟版",
           minv="System Director 7.x", adapter="meru_systemdirector",
           mgmt="Meru System Director", era="historic", status="待核对")

# Ubiquiti UniFi 官方 Legacy / Vintage 清单
fam("UniFi 官方 Legacy 第一代 (EOL)")
legacy_ap("Ubiquiti UniFi", "UAP (v1)|UAP (v2)|UAP-LR (v1)|UAP-LR (v2)|UAP-Pro|UAP-IW|UAP-Outdoor|UAP-Outdoor+|UAP-Outdoor-5|UAP-AC|UAP-AC-Outdoor|UAP-AC-EDU|UAP-AC-IW-PRO",
          "Wi-Fi 4", "2.4/5", "Ubiquiti 官方 Vintage & Legacy 清单；第一代 UAP 于 2021-03 停止更新，新版 UniFi Network 不再支持",
          minv="UniFi Network 5.x（旧 Java 控制器）", adapter="unifi_controller_legacy",
          mgmt="UniFi Network 5.x 自托管", status="已核实")
fam("UniFi AC 系列存量 (Wi-Fi 5)")
legacy_ap("Ubiquiti UniFi", "UAP-AC-Lite|UAP-AC-LR|UAP-AC-Pro|UAP-AC-M|UAP-AC-M-PRO|UAP-AC-IW|UAP-AC-HD|UAP-AC-SHD|UAP-nanoHD|UAP-FlexHD|UAP-BeaconHD|UAP-IW-HD|UAP-XG|UWB-XG",
          "Wi-Fi 5", "2.4/5", "Ubiquiti 官方 RoHS 型号清单 / 安装支架兼容表；AC 系列属存量，仍可由 UniFi Network 自托管管理",
          minv="UniFi Network 6.x / 7.x", adapter="unifi_controller_legacy",
          mgmt="UniFi Network 自托管", status="已核实")
legacy_ctl("Ubiquiti UniFi", "UniFi Network Controller 5.x|Cloud Key Gen1 (UC-CK)|UniFi Application Server",
           "旧版 Java 控制器 / 第一代 Cloud Key / 应用服务器（官方列为 Legacy）",
           minv="UniFi Network 5.x", adapter="unifi_controller_legacy",
           mgmt="UniFi Network 旧版", plane="虚拟嵌入式", status="已核实")

# Cambium / 国内老品
fam("Cambium cnPilot 存量 AP")
legacy_ap("Cambium", "cnPilot e400|cnPilot e410|cnPilot e430|cnPilot e500|cnPilot e501S|cnPilot e600|cnPilot e700|cnPilot r190V|cnPilot r200P|cnPilot r201P",
          "Wi-Fi 4", "2.4/5", "Cambium cnPilot 存量线；按现网铭牌核对",
          minv="cnMaestro On-Premises 2.x", adapter="cambium_cnmaestro_onprem",
          mgmt="cnMaestro On-Premises", status="待核对")
legacy_ctl("Cambium", "cnMaestro On-Premises",
           "cnMaestro 本地部署版（内网离线可用）",
           minv="cnMaestro 2.x", adapter="cambium_cnmaestro_onprem",
           mgmt="cnMaestro 本地", plane="虚拟嵌入式", status="待核对")

fam("华为 AP2000~AP9000 系列 802.11n (EOL)")
legacy_ap("华为", "AP3010DN-AGN|AP3010DN-AGN-FAT|AP3010DN-V2|AP5010DN-AGN|AP5010DN-AGN-FAT|AP5010SN-GN|AP6010DN-AGN|AP6010DN-AGN-FAT|AP6010SN-GN|AP6310SN-GN|AP6510DN-AGN|AP6510DN-AGN-FAT|AP6610DN-AGN|AP6610DN-AGN-FAT|AP7110DN-AGN",
          "Wi-Fi 4", "2.4/5", "华为官方产品列表；政府采购文件确认 AC6605 V200R005C10 + AP6010DN 已停产停供，新 AirEngine 不再兼容该软件版本；AP6610DN-AGN 2023-06-30 停止服务",
          minv="V200R005C10 / V200R006 / V200R008", adapter="huawei_ac_v2r5",
          mgmt="AC6605 / AC6005（旧软件分支）", status="已核实")
fam("华为 AP2000~AP9000 系列 802.11ac (EOL)")
legacy_ap("华为", "AP2030DN|AP2050DN|AP2050DN-E|AP2051DN|AP2051DN-E|AP2051DN-S|AP3030DN|AP3030DN-FAT|AP4030DN|AP4030DN-E|AP4030TN|AP4050DN|AP4050DN-E|AP4050DN-HD|AP4050DN-S|AP4051TN|AP430-E|AP5030DN|AP5030DN-FAT|AP5030DN-S|AP5130DN|AP6050DN|AP6052DN|AP6150DN|AP7030DE|AP7050DE|AP7052DN|AP7152DN|AP8030DN|AP8050TN-HD|AP8130DN|AP8130DN-FAT|AP8150DN|AP9130DN-FAT|AP9330DN",
          "Wi-Fi 5", "2.4/5", "华为官方技术支持全部产品列表（AP2000~AP9000 系列）；Fit/Fat 双模式，旧 AC 软件分支管理",
          minv="V200R006 / V200R008 / V200R010", adapter="huawei_ac_v2r5",
          mgmt="AC6605 / AC6005 / AC6805（旧分支）", status="已核实")
fam("华为 旧 AC 板卡与网管")
legacy_ctl("华为", "ACU2 (S7700/S9700 无线控制板卡)|Agile Controller-Campus|eSight WLAN 组件|iManager U2000 WLAN",
           "旧 AC 板卡 / 敏捷控制器 / 网管组件；与 AirEngine 新平台不兼容",
           minv="V200R005 / V200R008", adapter="huawei_ac_v2r5",
           mgmt="ACU2 / Agile Controller / eSight", status="待核对")

fam("H3C WA 早期 802.11n (EOL)")
legacy_ap("H3C", "WA1208E-GNP|WA2110-GN|WA2210E-GE|WA2610-GN|WA2610-GNE|WA2610E-GNE|WA2610E-GNP|WA2610H-GN|WA2610H-GN-H20|WA2610i-GN|WA2610X|WA2612|WA2620|WA2620-AGN|WA2620-AGN-C|WA2620-AGN-S|WA2620E|WA2620i-AGN|WA2620X|WA3610i-GN|WA3620i-AGN|WA3628i-AGN|WA3628i-AGN-X|WA4310-TB",
          "Wi-Fi 4", "2.4/5", "H3C 官方 AC-AP License 兼容列表（WX2560H / WX3520H）与无线产品下载页；WA26/36 系列需对应旧软件版本",
          minv="WX R5451 / R5457 旧分支", adapter="h3c_wx_legacy",
          mgmt="WX / WAC / MSG360", status="已核实")
fam("H3C WA43xx / WA53xx 存量 802.11ac")
legacy_ap("H3C", "WA4320-ACN|WA4320-ACN-B|WA4320-ACN-C|WA4320-ACN-D|WA4320-ACN-E|WA4320-ACN-PI|WA4320-ACN-SI|WA4320H|WA4320H-ACN|WA4320H-SI|WA4320H-EI|WA4320i-ACN|WA4320i-X|WA4320X|WA4330-ACN|WA4620i-ACN|WA5320H|WA5320-SI|WA5620|WA5620i-CAN",
          "Wi-Fi 5", "2.4/5", "H3C 官方《WLAN 特性与硬件适配关系》Wi-Fi4/Wi-Fi5 AP 款型表；WA4320 系列为园区存量主力",
          minv="WX R5451 / WAC 对应版本", adapter="h3c_wx_legacy",
          mgmt="WX / WAC / MSG360", status="已核实")
fam("H3C 小贝 / Mini 存量 AP")
legacy_ap("H3C", "WAP621|WAP712C|WAP722|WAP722E|WAP722S|WAP422|WAP422S|Mini A20|Mini A21|Mini A31|Mini A50|Mini A51|Mini NW8321C",
          "Wi-Fi 4", "2.4", "H3C 小贝 / Mini 分销与 SOHO 线；按现网铭牌核对",
          minv="WAC / MSG360", adapter="h3c_wx_legacy",
          mgmt="WAC360/361/WAC380/MSG360", form="面板/小型", status="待核对")
fam("H3C 早期 AC / 网管")
legacy_ctl("H3C", "WAC360|WAC361|WAC380-30|WAC380-60|WAC380-90|WAC380-120|MSG360-10|MSG360-20|MSG360-20N|MSG360-40|WX2560H|iMC WSM 无线业务管理",
           "H3C 官方无线产品下载页与 License 兼容列表；WX2560H 最多管理 128 AP；iMC WSM 为网管组件",
           minv="WAC / MSG / WX R5451 分支", adapter="h3c_wx_legacy",
           mgmt="WAC / MSG360 / WX 系列", status="已核实")
legacy_ctl("H3C", "WX5000 系列|WX3000 系列",
           "H3C 更早期 AC 系列；按现网铭牌与软件版本核对",
           minv="WX 旧版", adapter="h3c_wx_legacy",
           mgmt="WX 系列", status="待核对")

fam("锐捷 2016 年前老品 AP (EOL)")
legacy_ap("锐捷", "AP110-W|AP120-M|AP120-W|AP120-S|AP130-W|AP130(L)|AP130(W)|AP130(W2)|AP220-SE|AP220-I|AP220-SI|AP220-E|AP220-E(M)|AP220-E(M)-V2|AP220-SH|AP220-SH(C)|AP220-E(C)|AP220-E(P)|AP320-I|AP3220-S|AP3220-D|AP620-H|AP620-H(-S)|AP620-H(C)",
          "Wi-Fi 4", "2.4/5", "锐捷官方《AC 支持管理的 AP 型号列表》2016 年前老品清单；型号为官方简写，等价于 RG-AP 前缀写法；WS3302/WS5302 不再提供 11.1(5)B9 及以后 11.x，无法管理 B9+ 新品 AP",
          minv="RGOS 10.4(1b19) / 11.1(5)B8", adapter="ruijie_ws_legacy",
          mgmt="RG-WS3302 / WS5302 / WS5504 / WS5708 / M8600-WS", status="已核实")
fam("锐捷 老品 802.11ac / 智分 / 零漫游 AP (EOL)")
legacy_ap("锐捷", "AP520-I|AP520|AP530-I|AP530-I(V2)|AP520-I(G2)|AP630(IDA)|AP630(IODA)|RG-AP630|RG-AP630(CD)|MAP552(SR)|MAP552|MAP552-W|AP5280|AP4210|APD-M|APD-M(AC)|AM5528|AM5514|AM5528(EP)|AM5528(ES)|AM5532|MAP752(T)|MAP752(G)|MAP752(E)|MAP752(S)|AP530-I(S1)|AP530-I(S2)|AP530-I(S3)",
          "Wi-Fi 5", "2.4/5", "锐捷官方 AC-AP 兼容列表与《无线产品 AC&AP 参数表》；智分 / 零漫游 / 地铁 AP 为专用形态",
          minv="RGOS 10.4(1b19) / 11.x", adapter="ruijie_ws_legacy",
          mgmt="RG-WS 系列旧 AC", form="面板/智分/专用", status="已核实")
fam("锐捷 早期 AC / 网管 (EOL)")
legacy_ctl("锐捷", "RG-WS3302|RG-WS5302|RG-WS5504|RG-WS5708|RG-WS6024|RG-M6000-WS|RG-SNC",
           "锐捷官方 AC 兼容列表与参数表：WS3302/WS5302 止于 11.1(5)B8；其余 WS5 旧 AC 对 2016 年后新品 AP 限制 100 license；RG-SNC 为统一网管",
           minv="RGOS 10.4(1b19) / 11.1(5)B8", adapter="ruijie_ws_legacy",
           mgmt="RG-WS 系列 / RG-SNC", status="已核实")

fam("TP-LINK 商用 早期 AC / AP")
legacy_ap("TP-LINK 商用", "TL-AP300I-PoE|TL-AP450I-PoE|TL-AP900C-PoE|TL-AP1200GI-PoE(旧版)|TL-AP1750C-PoE(旧版)",
          "Wi-Fi 4", "2.4/5", "商用线早期吸顶 / 面板 AP；具体 SKU 按现网铭牌核对",
          minv="TL-AC 旧版", adapter="tplink_ac_legacy",
          mgmt="TL-AC 系列", status="待核对")
legacy_ctl("TP-LINK 商用", "TL-AC100|TL-AC300|TL-AC2000|TL-AC5000",
           "商用线早期 AC；具体 SKU 按现网铭牌核对",
           minv="TL-AC 旧版", adapter="tplink_ac_legacy",
           mgmt="TL-AC 系列", status="待核对")

fam("老旧设备通用降级档案")
rec("通用", "Fallback", "EOL 未识别型号(通用降级档案)", "降级档案", "待核对", "-", "-",
    "-", "-", "预留", "铭牌无法识别的老旧设备：通用 SNMP v2c + 手工射频参数 + 实测打点",
    mgmt="通用 SNMP v2c + 手工参数")

set_era("modern")

# ---------------- 统计与产物 ----------------
vendors = sorted(set(r["vendor"] for r in rows))
stats = {
    "total": len(rows),
    "vendors": len(vendors),
    "vendor_list": vendors,
    "domestic": sum(1 for r in rows if r["origin"] == "国内"),
    "overseas": sum(1 for r in rows if r["origin"] == "国外"),
    "ap": sum(1 for r in rows if r["category"] == "AP"),
    "controller": sum(1 for r in rows if r["category"] == "Controller"),
    "management_plane": sum(1 for r in rows if r["category"] == "ManagementPlane"),
    "wifi7": sum(1 for r in rows if r["wifi_gen"] == "Wi-Fi 7"),
    "verified": sum(1 for r in rows if r["verify_status"] == "已核实"),
    "pending": sum(1 for r in rows if r["verify_status"] == "待核对"),
    "placeholder": sum(1 for r in rows if r["verify_status"] == "预留"),
    "by_vendor": dict(Counter(r["vendor"] for r in rows)),
    "legacy_eol": sum(1 for r in rows if r["lifecycle"] == "EOL 已停产"),
    "legacy_stock": sum(1 for r in rows if r["lifecycle"] == "存量在用"),
    "historic_brand": sum(1 for r in rows if r["lifecycle"] == "历史品牌"),
    "legacy_total": sum(1 for r in rows if r["lifecycle"] in ("EOL 已停产", "存量在用", "历史品牌")),
    "by_lifecycle": dict(Counter(r["lifecycle"] for r in rows)),
    "by_capability": dict(Counter(r["capability_tier"] for r in rows)),
    "wifi4_or_older": sum(1 for r in rows if r["wifi_gen"] in ("Wi-Fi 4", "802.11a/b/g")),
}

import io


def write_atomic(path, text, encoding):
    """先写临时文件再原子替换；目标被 Excel/WPS 独占时输出到带版本号的替代文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding=encoding) as f:
        f.write(text)
    try:
        os.replace(tmp, path)
        return path
    except OSError as e:
        stem, ext = os.path.splitext(os.path.basename(path))
        alt = os.path.join(BASE, stem + "-v" + VERSION + ext)
        os.replace(tmp, alt)
        print("WARN: 目标文件被占用，未能原地写入:", path, "->", e)
        print("WARN: 已输出替代文件:", alt, "（关闭 Excel/WPS 后重跑本脚本即可同步原文件）")
        return alt


csv_path = os.path.join(BASE, "device-model-catalog.csv")
buf = io.StringIO()
w = csv.DictWriter(buf, fieldnames=FIELDS)
w.writeheader()
w.writerows(rows)
csv_actual = write_atomic(csv_path, buf.getvalue(), "utf-8-sig")

json_path = os.path.join(BASE, "device-model-catalog.json")
payload = {
    "catalog_version": VERSION,
    "generated_at": DATE,
    "description": "无线网优工具设备型号库：国内外主流 + 老旧/EOL/历史品牌 AP / WLC / 管理平面全量目录",
    "fields": FIELDS,
    "stats": stats,
    "records": rows,
}
json_actual = write_atomic(json_path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>设备型号库 · 主流 + 老旧 / EOL 存量 AP / WLC / 管理平面</title>
<style>
:root{--bg:#fff;--page:#f6f7f9;--ink:#1f2328;--ink2:#4a5158;--ink3:#7b838c;--line:#e3e6ea;--blue:#185fa5;--blue-bg:#e6f1fb;--teal:#0f6e56;--teal-bg:#e1f5ee;--amber:#854f0b;--amber-bg:#faeeda;--green:#3b6d11;--green-bg:#eaf3de}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;font-size:13px;line-height:1.6}
.wrap{max-width:1400px;margin:0 auto;padding:0 22px 60px}
header{background:var(--bg);border-bottom:1px solid var(--line);padding:26px 0 22px;margin-bottom:18px}
header .wrap{padding-bottom:0}
h1{font-size:22px;font-weight:600;margin:0 0 6px}
.sub{color:var(--ink2);font-size:13px;margin:0}
.cards{display:grid;grid-template-columns:repeat(7,1fr);gap:10px;margin:18px 0}
.card{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:12px 14px}
.card .v{font-size:21px;font-weight:600;color:var(--blue);line-height:1.2}
.card .k{font-size:11.5px;color:var(--ink3);margin-top:3px}
.panel{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:14px 16px;margin-bottom:14px}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
input[type=text],select{font:inherit;font-size:12.5px;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 9px;outline:none}
input[type=text]{min-width:250px;flex:1}
input[type=text]:focus,select:focus{border-color:var(--blue)}
button{font:inherit;font-size:12.5px;cursor:pointer;background:var(--blue-bg);color:var(--blue);border:1px solid var(--blue);border-radius:6px;padding:6px 12px}
button.ghost{background:var(--bg);color:var(--ink2);border-color:var(--line)}
.bar{display:flex;justify-content:space-between;align-items:center;margin:12px 0 8px;font-size:12.5px;color:var(--ink2)}
.tbl{background:var(--bg);border:1px solid var(--line);border-radius:9px;overflow:auto;max-height:70vh}
table{width:100%;border-collapse:collapse;font-size:12px}
th{position:sticky;top:0;background:var(--page);text-align:left;font-weight:600;color:var(--ink2);border-bottom:1px solid var(--line);padding:9px 10px;white-space:nowrap;z-index:2}
td{border-bottom:1px solid var(--line);padding:7px 10px;color:var(--ink2);vertical-align:top;white-space:nowrap}
tr:hover td{background:#fbfcfd}
td.model{font-weight:600;color:var(--ink);font-family:Consolas,"SF Mono",monospace}
.tag{display:inline-block;font-size:11px;border-radius:4px;padding:1px 6px}
.t-cn{background:var(--blue-bg);color:var(--blue)}
.t-ov{background:var(--teal-bg);color:var(--teal)}
.t-ok{background:var(--green-bg);color:var(--green)}
.t-pend{background:var(--amber-bg);color:var(--amber)}
.t-ph{background:#f1efe8;color:#5f5e5a}
.t-eol{background:#fdeaea;color:#a33030}
.t-stock{background:var(--amber-bg);color:var(--amber)}
.t-hist{background:#eeeaf7;color:#5b4390}
.t-a{background:var(--green-bg);color:var(--green)}
.t-b{background:var(--blue-bg);color:var(--blue)}
.t-c{background:#fdeaea;color:#a33030}
.note{background:var(--blue-bg);border-left:3px solid var(--blue);border-radius:0 6px 6px 0;padding:11px 15px;font-size:12.5px;color:#0c447c;margin:14px 0}
footer{color:var(--ink3);font-size:11.5px;text-align:center;padding:18px 0}
@media(max-width:1000px){.cards{grid-template-columns:repeat(3,1fr)}}
@media print{.controls,.tbl{max-height:none}th{position:static}button{display:none}}
</style>
</head>
<body>
<header><div class="wrap">
<h1>设备型号库 · 主流 + 老旧 / EOL 存量 AP / WLC / 管理平面</h1>
<p class="sub">全量目录 v__VERSION__ · 核对日期 __DATE__ · 共 __TOTAL__ 条 / __VENDORS__ 家厂商，其中老旧 / EOL / 存量 __LEGACY__ 条 · 数据同源自 device-model-catalog.csv</p>
</div></header>
<div class="wrap">
<div class="cards">
<div class="card"><div class="v">__TOTAL__</div><div class="k">型号记录总数</div></div>
<div class="card"><div class="v">__VENDORS__</div><div class="k">覆盖厂商（含历史品牌）</div></div>
<div class="card"><div class="v">__DOMESTIC__</div><div class="k">国内厂商型号</div></div>
<div class="card"><div class="v">__OVERSEAS__</div><div class="k">国外厂商型号</div></div>
<div class="card"><div class="v">__LEGACY__</div><div class="k">老旧 / EOL / 存量</div></div>
<div class="card"><div class="v">__WIFI7__</div><div class="k">Wi-Fi 7 型号</div></div>
<div class="card"><div class="v">__VERIFIED__</div><div class="k">已核实条目</div></div>
</div>
<div class="panel">
<div class="controls">
<input type="text" id="q" placeholder="搜索型号 / 厂商 / 系列 / 适配器 / 备注">
<select id="origin"><option value="">全部产地</option><option>国内</option><option>国外</option><option>通用</option></select>
<select id="category"><option value="">全部类别</option><option>AP</option><option>Controller</option><option>ManagementPlane</option><option>Fallback</option></select>
<select id="vendor"><option value="">全部厂商</option></select>
<select id="gen"><option value="">全部世代</option><option>802.11a/b/g</option><option>Wi-Fi 4</option><option>Wi-Fi 5</option><option>Wi-Fi 6</option><option>Wi-Fi 6E</option><option>Wi-Fi 7</option><option>混合</option></select>
<select id="lifecycle"><option value="">全部生命周期</option><option>主流在售</option><option>存量在用</option><option>EOL 已停产</option><option>历史品牌</option><option>预留</option></select>
<select id="tier"><option value="">全部能力层级</option><option>A-完整</option><option>B-受限</option><option>C-降级</option></select>
<select id="status"><option value="">全部状态</option><option>已核实</option><option>待核对</option><option>预留</option></select>
<button class="ghost" id="reset">重置</button>
<button id="export">导出当前结果 CSV</button>
</div>
<div class="bar"><span id="count"></span><span>来源：厂商官网 / 官方兼容矩阵 / EOL 公告；待核对与预留条目需按现网清单确认</span></div>
</div>
<div class="tbl"><table>
<thead><tr><th>型号</th><th>厂商</th><th>产地</th><th>系列</th><th>类别</th><th>世代</th><th>频段</th><th>形态</th><th>管理平面</th><th>适配器</th><th>最低版本</th><th>生命周期</th><th>能力层级</th><th>核实状态</th><th>备注</th></tr></thead>
<tbody id="tbody"></tbody>
</table></div>
<div class="note"><b>生命周期：</b>主流在售为当前产品线；存量在用仍常见于现网但已非主推；EOL 已停产按厂商 EOL 公告、兼容矩阵与官方产品列表整理，<code>min_version</code> 记录该老设备可用的最后一版控制器固件；历史品牌为已被收购 / 退市的产品线，仅按铭牌识别。<b>能力层级：</b>A-完整（现代 API + 遥测）、B-受限（SNMP / 2.4+5 GHz）、C-降级（通用 SNMP v2c + 手工参数 + 实测打点，客户端级指标不可用）。<b>核实状态：</b>已核实来自本轮检索到的官方资料；待核对按公开目录整理；预留为占位或降级档案。型号库是可维护数据，修改 build_catalog.py 后重新运行即可同步刷新 CSV、JSON 与本页。</div>
<footer>无线网优工具 · 设备型号库 v__VERSION__ · __DATE__</footer>
</div>
<script>
const DATA = __DATA__;
const FIELDS = __FIELDS__;
const STATS = __STATS__;
const $=function(id){return document.getElementById(id);};
const q=$("q"),origin=$("origin"),category=$("category"),vendor=$("vendor"),gen=$("gen"),lifecycle=$("lifecycle"),tier=$("tier"),status=$("status"),tbody=$("tbody"),count=$("count");
STATS.vendor_list.forEach(function(v){const o=document.createElement("option");o.textContent=v;vendor.appendChild(o);});
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
function tag(s){if(s==="国内")return '<span class="tag t-cn">国内</span>';if(s==="国外")return '<span class="tag t-ov">国外</span>';return esc(s);}
function stg(s){if(s==="已核实")return '<span class="tag t-ok">已核实</span>';if(s==="待核对")return '<span class="tag t-pend">待核对</span>';return '<span class="tag t-ph">预留</span>';}
function lifetag(s){if(s==="EOL 已停产")return '<span class="tag t-eol">EOL 已停产</span>';if(s==="存量在用")return '<span class="tag t-stock">存量在用</span>';if(s==="历史品牌")return '<span class="tag t-hist">历史品牌</span>';return esc(s);}
function tiertag(s){if(s==="A-完整")return '<span class="tag t-a">A-完整</span>';if(s==="B-受限")return '<span class="tag t-b">B-受限</span>';return '<span class="tag t-c">C-降级</span>';}
let VIEW=DATA;
function render(){
 const kw=q.value.trim().toLowerCase();
 VIEW=DATA.filter(function(r){
  if(origin.value&&r.origin!==origin.value)return false;
  if(category.value&&r.category!==category.value)return false;
  if(vendor.value&&r.vendor!==vendor.value)return false;
  if(gen.value&&r.wifi_gen!==gen.value)return false;
  if(lifecycle.value&&r.lifecycle!==lifecycle.value)return false;
  if(tier.value&&r.capability_tier!==tier.value)return false;
  if(status.value&&r.verify_status!==status.value)return false;
  if(kw){const s=[r.models,r.vendor,r.family,r.adapter_id,r.note,r.mgmt_examples,r.lifecycle,r.capability_tier].join(" ").toLowerCase();if(s.indexOf(kw)<0)return false;}
  return true;
 });
 count.textContent="显示 "+VIEW.length+" 条 / 共 "+DATA.length+" 条";
 tbody.innerHTML=VIEW.map(function(r){return "<tr><td class='model'>"+esc(r.models)+"</td><td>"+esc(r.vendor)+"</td><td>"+tag(r.origin)+"</td><td>"+esc(r.family)+"</td><td>"+esc(r.category)+"</td><td>"+esc(r.wifi_gen)+"</td><td>"+esc(r.bands)+"</td><td>"+esc(r.form_factor)+"</td><td>"+esc(r.plane_type)+"</td><td>"+esc(r.adapter_id)+"</td><td>"+esc(r.min_version)+"</td><td>"+lifetag(r.lifecycle)+"</td><td>"+tiertag(r.capability_tier)+"</td><td>"+stg(r.verify_status)+"</td><td>"+esc(r.note)+"</td></tr>";}).join("");
}
[q,origin,category,vendor,gen,lifecycle,tier,status].forEach(function(el){el.addEventListener("input",render);el.addEventListener("change",render);});
$("reset").addEventListener("click",function(){[q,origin,category,vendor,gen,lifecycle,tier,status].forEach(function(el){el.value="";});render();});
$("export").addEventListener("click",function(){
 const lines=[FIELDS.join(",")];
 VIEW.forEach(function(r){lines.push(FIELDS.map(function(f){return '"'+String(r[f]).replace(/"/g,'""')+'"';}).join(","));});
 const blob=new Blob(["\\ufeff"+lines.join("\\r\\n")],{type:"text/csv;charset=utf-8"});
 const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="device-model-catalog-filtered.csv";a.click();URL.revokeObjectURL(a.href);
});
render();
</script>
</body>
</html>
"""

html_out = (HTML
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
            .replace("__FIELDS__", json.dumps(FIELDS, ensure_ascii=False))
            .replace("__STATS__", json.dumps(stats, ensure_ascii=False, separators=(",", ":")))
            .replace("__VERSION__", VERSION)
            .replace("__DATE__", DATE)
            .replace("__TOTAL__", str(stats["total"]))
            .replace("__VENDORS__", str(stats["vendors"]))
            .replace("__DOMESTIC__", str(stats["domestic"]))
            .replace("__OVERSEAS__", str(stats["overseas"]))
            .replace("__WIFI7__", str(stats["wifi7"]))
            .replace("__VERIFIED__", str(stats["verified"]))
            .replace("__LEGACY__", str(stats["legacy_total"])))
html_actual = write_atomic(os.path.join(BASE, "device-model-library.html"), html_out, "utf-8")

print("outputs:", csv_actual, "|", json_actual, "|", html_actual)
print("records:", stats["total"])
print("vendors:", stats["vendors"])
print("domestic/overseas:", stats["domestic"], stats["overseas"])
print("ap/controller/mgmt:", stats["ap"], stats["controller"], stats["management_plane"])
print("wifi7:", stats["wifi7"])
print("verified/pending/placeholder:", stats["verified"], stats["pending"], stats["placeholder"])
print("legacy_total/eol/stock/historic:", stats["legacy_total"], stats["legacy_eol"],
      stats["legacy_stock"], stats["historic_brand"])
print("wifi4_or_older:", stats["wifi4_or_older"])
print("by_lifecycle:", json.dumps(stats["by_lifecycle"], ensure_ascii=False))
print("by_capability:", json.dumps(stats["by_capability"], ensure_ascii=False))
print("by_vendor:", json.dumps(stats["by_vendor"], ensure_ascii=False))
