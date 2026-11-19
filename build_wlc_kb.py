# -*- coding: utf-8 -*-
"""生成 WLC 诊断知识库种子：厂商接入矩阵 + 可溯源诊断规则。

单一数据源，输出：
  wlc-vendor-adapters.csv      主流 WLC / 管理平面的采集通道与官方文档入口
  wlc-diagnostic-rules.json    错误包、日志、配置类诊断规则（每条带官方引用）

设计原则：工具离线运行，只存「索引 + 摘要 + 官方链接 + 适用版本 + 核对日期」，
不整篇内置厂商文档；每条结论必须能回查到来源，未核实的条目标记待核对。
"""
import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATE = "2026-11-10"

# ---------------------------------------------------------------- 厂商接入矩阵
VENDOR_FIELDS = [
    "vendor", "origin", "platform", "plane_type", "sw_examples",
    "collect_channels", "config_snapshot_source", "packet_evidence_source",
    "error_code_source", "official_doc_portal", "doc_url", "doc_version",
    "adapter_id", "priority", "priority_reason", "verify_status",
    "verified_date", "note",
]

VENDORS = [
    dict(
        vendor="Cisco", origin="国外", platform="Catalyst 9800 / 9800-CL（IOS XE）",
        plane_type="独立控制器", sw_examples="IOS XE 17.6 / 17.9 / 17.12 / 17.15 / 17.18",
        collect_channels="RESTCONF/NETCONF-YANG（netconf-yang，端口 830）；SSH CLI 只读；syslog；gRPC telemetry（telemetry ietf subscription）；SNMP v2c",
        config_snapshot_source="show running-config / NETCONF get-config（WLAN Profile、Policy Profile、Policy/Site/RF Tag）",
        packet_evidence_source="show tech-support wireless；debug wireless mac（Radioactive Trace）；show logging profile wireless filter mac ... to-file always-on-；GUI Troubleshooting > Packet Capture；monitor capture",
        error_code_source="Cisco DevNet Client Delete Reasons（CO_CLIENT_DELETE_REASON_* / MN_*）； syslog / RA trace 状态码",
        official_doc_portal="Cisco Catalyst 9800 Configuration Guide / DevNet Wireless Troubleshooting Tools",
        doc_url="https://developer.cisco.com/docs/wireless-troubleshooting-tools/client-delete-reasons",
        doc_version="IOS XE 17.x", adapter_id="cisco_9800_restconf",
        priority="P1", priority_reason="现网 Cisco 体系，文档最完整、错误码字典可直接规则化",
        verify_status="official-doc", verified_date=DATE,
        note="Wi-Fi 7 AP 需 IOS XE 17.15.2+；兼容矩阵见方案书第 14.6 节",
    ),
    dict(
        vendor="Cisco", origin="国外", platform="AireOS WLC（5508 / 5520 / 8540 / 3504）",
        plane_type="独立控制器（EOL）", sw_examples="AireOS 8.5 / 8.10",
        collect_channels="SSH CLI 只读；SNMP v2c（CISCO-LWAPP-* 私有 MIB）；syslog",
        config_snapshot_source="show run-config / show wlan summary / show ap summary",
        packet_evidence_source="debug client <MAC>；debug capwap；show traplog；show msglog",
        error_code_source="AireOS 客户端排除码 / traplog；官方 Troubleshoot 文档",
        official_doc_portal="Cisco AireOS WLC Configuration / Troubleshooting Guide",
        doc_url="https://www.cisco.com/c/en/us/support/wireless/5500-series-wireless-controllers/products-installation-and-configuration-guides-list.html",
        doc_version="8.5 / 8.10", adapter_id="cisco_aireos_ssh",
        priority="P2", priority_reason="老旧平台，仅当现网仍有存量设备时投入专用适配器",
        verify_status="needs-field-check", verified_date=DATE,
        note="无现代 RESTCONF；老 MIB 需按现场版本核对 OID",
    ),
    dict(
        vendor="Cisco", origin="国外", platform="Meraki MR（云管）/ Catalyst Center",
        plane_type="云管理平面", sw_examples="Meraki Dashboard / Catalyst Center 2.x",
        collect_channels="Dashboard REST API；Catalyst Center Intent API；Webhook / syslog 导出",
        config_snapshot_source="Dashboard API：/networks/{id}/wireless/ssids、RF profiles",
        packet_evidence_source="Dashboard 事件日志 / Client 连接失败原因；Catalyst Center Assurance",
        error_code_source="Meraki 事件类型与连接失败原因码",
        official_doc_portal="Meraki Dashboard API Documentation / Cisco DevNet",
        doc_url="https://developer.cisco.com/meraki/api-v1/",
        doc_version="API v1", adapter_id="meraki_dashboard_api",
        priority="P3", priority_reason="现网未见，且云管需外网/API Key，与内网离线定位冲突",
        verify_status="needs-field-check", verified_date=DATE,
        note="无本地控制器实体，外键指向 ManagementPlane",
    ),
    dict(
        vendor="Fortinet", origin="国外", platform="FortiGate 内置无线控制器（FortiAP）",
        plane_type="防火墙内置控制器", sw_examples="FortiOS 6.4 / 7.0 / 7.2 / 7.4 / 7.6",
        collect_channels="FortiOS REST API；SSH CLI diagnose（只读）；SNMP v2c；syslog / FortiAnalyzer",
        config_snapshot_source="show wireless-controller wtp / vap / wtp-profile / ssid；config 备份",
        packet_evidence_source="diagnose sniff packet \"port 5246 or port 5247\" 6 0 l（可转 PCAP）；diagnose debug application cw_acd 0x7ff；diagnose wireless-controller wlac wtp_filter / sta_filter",
        error_code_source="diagnose wireless-controller wlac -c wtp 的 last failure / last failure param；cw_acd debug；diagnose debug crashlog read",
        official_doc_portal="Fortinet Document Library：FortiWiFi and FortiAP Configuration Guide / FortiGate Administration Guide",
        doc_url="https://docs.fortinet.com/document/fortiap/7.6.6/fortiwifi-and-fortiap-configuration-guide/266575",
        doc_version="FortiAP 7.6.6 / FortiOS 7.x", adapter_id="fortigate_wlc_cli_api",
        priority="P1", priority_reason="现网已有 Fortinet 400F；CLI 抓包与 sta/wtp debug 可直接落地",
        verify_status="official-doc", verified_date=DATE,
        note="控制器实体是 FortiGate + FortiOS 版本，不是独立 WLC；CAPWAP 走 UDP 5246/5247",
    ),
    dict(
        vendor="Fortinet", origin="国外", platform="FortiLAN Cloud / FortiAP 云管",
        plane_type="云管理平面", sw_examples="FortiLAN Cloud",
        collect_channels="云 REST API；FortiAP 本地 CLI；SNMP",
        config_snapshot_source="云侧 AP 配置模板导出",
        packet_evidence_source="FortiAP 本地 diag_sniffer any \"port 5246 or port 5247\"",
        error_code_source="云侧事件日志 / AP 本地日志",
        official_doc_portal="Fortinet Document Library：FortiLAN Cloud",
        doc_url="https://docs.fortinet.com/product/fortilan-cloud/",
        doc_version="current", adapter_id="fortilan_cloud_api",
        priority="P3", priority_reason="需外网与账号授权，非现网主形态",
        verify_status="needs-field-check", verified_date=DATE, note="降级策略：离线时用本地 CLI 证据",
    ),
    dict(
        vendor="RUCKUS", origin="国外", platform="SmartZone / vSZ / SZ100 / SZ300 / SZ144",
        plane_type="独立控制器", sw_examples="SZ 5.x / 6.0 / 7.1.1 / 7.3.0",
        collect_channels="REST API https://{host}:8443/wsg/api/public（serviceTicket）；设备内置 OpenAPI 文档 /wsg/apiDoc/openapi；SNMP v2c；syslog；GPB/MQTT 遥测",
        config_snapshot_source="REST API 导出 zone / wlan / ap-group 配置；CLI 配置备份",
        packet_evidence_source="AP 侧抓包；控制器 diagnostic / Troubleshooting and Diagnostics Guide 所述采集流程",
        error_code_source="REST 响应中的 Ruckus 私有错误码（如 152 备集群不允许操作、105 不支持的 API 版本）；Alarm and Event Reference Guide",
        official_doc_portal="RUCKUS Technical Documentation：Public API Reference / Alarm and Event Reference / Troubleshooting and Diagnostics",
        doc_url="https://docs.ruckuswireless.com/smartzone/6.0.0/vszh-public-api-reference-guide-600.html",
        doc_version="6.0 / 7.1.1 / 7.3.0", adapter_id="ruckus_smartzone_rest",
        priority="P2", priority_reason="API 规范公开且带 OpenAPI，适合第二批接入",
        verify_status="official-doc", verified_date=DATE,
        note="API 版本矩阵随 SZ 大版本变化（v5_0…v10_0）；7443/api/public 为已废弃前缀",
    ),
    dict(
        vendor="RUCKUS", origin="国外", platform="ZoneDirector 1000 / 1100 / 3000 / 5000（EOL）",
        plane_type="独立控制器（EOL）", sw_examples="ZD 9.8.3.0.58 / 9.10",
        collect_channels="SSH CLI；SNMP v2c；Web UI 导出",
        config_snapshot_source="配置备份文件 / show 命令输出",
        packet_evidence_source="AP 侧抓包 / 控制器 debug 日志",
        error_code_source="ZD 事件日志；官方 Release Notes 兼容说明",
        official_doc_portal="RUCKUS Support：ZoneDirector Release Notes / Command Reference",
        doc_url="https://support.ruckuswireless.com/documents",
        doc_version="9.8 / 9.9 / 9.10", adapter_id="ruckus_zonedirector",
        priority="P3", priority_reason="仅作历史存量识别与通用 SNMP 兜底",
        verify_status="needs-field-check", verified_date=DATE,
        note="ZD 与 AP 必须同版本；9.9 后不支持 7962/7762，9.10 后不支持 7025（见方案书 14.9）",
    ),
    dict(
        vendor="HPE Aruba Networking", origin="国外", platform="AOS 8 Mobility Conductor / Mobility Gateway",
        plane_type="独立控制器 +  conductor", sw_examples="ArubaOS 8.x",
        collect_channels="SSH CLI（show / 日志）；REST/北向接口（依赖版本与许可）；SNMP v2c；syslog",
        config_snapshot_source="show running-config / 配置层级 diff（AP group、AAA profile、VAP）",
        packet_evidence_source="AP 侧空口抓包；控制器 user-table / auth-tracebuf / 客户端 debug",
        error_code_source="auth-tracebuf 状态码；系统日志与安全公告",
        official_doc_portal="HPE Aruba Networking Technical Documentation / Support Portal",
        doc_url="https://www.arubanetworking.hpe.com/techdocs/",
        doc_version="AOS 8.x", adapter_id="aruba_aos8_cli",
        priority="P2", priority_reason="CLI 证据链成熟，但接口授权与版本差异需现场核对",
        verify_status="needs-field-check", verified_date=DATE,
        note="AOS 10 起管理平面迁移到 Aruba Central；2026-10 厂商宣布 Central 与 Mist 整合路线",
    ),
    dict(
        vendor="HPE Aruba Networking", origin="国外", platform="AOS 10 / Aruba Central",
        plane_type="云管理平面", sw_examples="AOS 10.x / Central",
        collect_channels="Central REST API（OAuth）；Webhook / 流式遥测；AP 本地 CLI",
        config_snapshot_source="Central 配置组 / 模板导出",
        packet_evidence_source="AP 侧抓包；Central 客户端事件流 / Audit log",
        error_code_source="Central 客户端失败原因码、告警与 Audit 事件",
        official_doc_portal="HPE Aruba Networking Central API / Developer Hub",
        doc_url="https://www.arubanetworking.hpe.com/techdocs/",
        doc_version="current", adapter_id="aruba_central_api",
        priority="P3", priority_reason="需云侧账号与外网，且厂商管理平面正在整合",
        verify_status="needs-field-check", verified_date=DATE,
        note="AOS 10 无本地 Mobility Conductor，按 ManagementPlane 建模",
    ),
    dict(
        vendor="Juniper / HPE Mist", origin="国外", platform="Mist Cloud / Marvis",
        plane_type="云管理平面", sw_examples="Mist Cloud",
        collect_channels="Mist REST API；Webhook / SSE 事件流；AP 本地 CLI",
        config_snapshot_source="Site / WLAN / RF template API 导出",
        packet_evidence_source="Mist 客户端事件（auth / DHCP / roam 失败）；Marvis 根因结论",
        error_code_source="Mist Client Events 中的 failure reason / SLE 分类器",
        official_doc_portal="Juniper Mist API Documentation / Marvis 文档",
        doc_url="https://www.juniper.net/documentation/product/us/en/mist/",
        doc_version="current", adapter_id="mist_cloud_api",
        priority="P3", priority_reason="云侧订阅与授权；与单机离线优先的定位不一致",
        verify_status="needs-field-check", verified_date=DATE,
        note="可作为「官方错误分类体系」的参考来源，不必先做采集适配器",
    ),
    dict(
        vendor="Huawei", origin="国内", platform="AirEngine WAC（AC6508 / AC6805 / X700 等）+ iMaster NCE-Campus",
        plane_type="独立控制器 + 网管平台", sw_examples="V600R024C10 / V600R025C00",
        collect_channels="iMaster NCE-Campus 北向 RESTCONF（/restconf/v1/data/ietf-alarms:alarms/alarm-list）；CampusInsight REST（/rest/fault/v1/current-alarms，X-Auth-Token）；AC SSH CLI；SNMP v2c；syslog",
        config_snapshot_source="display current-configuration；NCE 配置基线导出；display diagnostic-information 一键诊断包",
        packet_evidence_source="NCE 诊断信息收集（CPU/内存/异常重启/一键日志/KPI）；AC 侧报文捕获；AP 空口抓包",
        error_code_source="WLAN Log Reference（如 WLAN/4/hwApServiceInterruptAlertTrap_active，含「可能原因 / 处理步骤」）；北向告警接口",
        official_doc_portal="华为企业技术支持：WLAN Troubleshooting Guide / Log Reference / 北向接口指导",
        doc_url="https://support.huawei.cn/enterprise/zh/doc/EDOC1100458566/ca8ec7de",
        doc_version="V300R024C10 / V600R025C00", adapter_id="huawei_nce_restconf",
        priority="P1", priority_reason="信创/国内主流；Log Reference 自带「可能原因 + 处理步骤」，最适合直接编译成规则库",
        verify_status="official-doc", verified_date=DATE,
        note="WAC 诊断日志不支持自动收集（官方明确），需手动触发收集任务",
    ),
    dict(
        vendor="H3C", origin="国内", platform="WX 系列无线控制器（WX3500H / WX5500H 等）/ AD Campus",
        plane_type="独立控制器 + 网管平台", sw_examples="Comware V7 / AD Campus",
        collect_channels="SSH CLI；SNMP v2c；AD Campus 北向 REST（按现场版本）；syslog",
        config_snapshot_source="display current-configuration / 配置备份",
        packet_evidence_source="display wlan client / 诊断日志；AP 侧抓包",
        error_code_source="系统日志 / 告警参考（按现场版本配套文档核对）",
        official_doc_portal="新华三技术文档：无线控制器命令参考 / 日志参考 / 故障处理手册",
        doc_url="https://www.h3c.com/cn/Service/Document_Software/Document_Center/",
        doc_version="按现场版本", adapter_id="h3c_wx_cli",
        priority="P2", priority_reason="国内主流型号库已收录，需现场确认实际平台版本",
        verify_status="needs-field-check", verified_date=DATE,
        note="WX3500H 管 Wi-Fi 7 AP 的资料存在冲突，能力位需现场实测覆盖（方案书 14.6）",
    ),
    dict(
        vendor="锐捷 Ruijie", origin="国内", platform="RG-WS 系列 / WNOC / 云管平台",
        plane_type="独立控制器 + 云管", sw_examples="RGOS 11.x",
        collect_channels="SSH CLI；SNMP v2c；北向 REST（按版本）；syslog",
        config_snapshot_source="show running-config / 配置备份",
        packet_evidence_source="客户端上下线日志 / AP 抓包",
        error_code_source="系统日志与告警码（按现场版本文档核对）",
        official_doc_portal="锐捷官方资料中心：无线产品配置 / 故障处理手册",
        doc_url="https://www.ruijie.com.cn/fw/wd/",
        doc_version="按现场版本", adapter_id="ruijie_ws_cli",
        priority="P2", priority_reason="国内教育/园区场景常见，型号库已收录",
        verify_status="needs-field-check", verified_date=DATE,
        note="官方文档入口需现场以实际版本复核后再写入规则引用",
    ),
    dict(
        vendor="Ubiquiti / Extreme / 其他", origin="国外", platform="UniFi Network / ExtremeCloud IQ / IdentiFi",
        plane_type="软件控制器 / 云管", sw_examples="按现场版本",
        collect_channels="本地控制器 REST / SSH；SNMP；syslog；云 API（需授权）",
        config_snapshot_source="控制器配置导出",
        packet_evidence_source="客户端事件日志 / AP 抓包",
        error_code_source="控制器事件码（因版本差异大，统一走通用规则）",
        official_doc_portal="各厂商官方社区与文档中心",
        doc_url="", doc_version="按现场版本", adapter_id="generic_wlc_snmp",
        priority="P3", priority_reason="现网未见，只做通用 SNMP + 日志兜底，不投专用适配器",
        verify_status="needs-field-check", verified_date=DATE,
        note="统一降级到通用 SNMP v2c 与 syslog 规则，不承诺厂商级错误码解读",
    ),
]

# ---------------------------------------------------------------- 诊断规则库
RULE_FIELDS = [
    "id", "category", "scope", "vendors", "symptom", "evidence",
    "detection", "severity", "conclusion", "remediation",
    "doc_refs", "verify_status", "priority", "note",
]


def ref(title, url, version="", accessed=DATE, status="official-doc"):
    return dict(title=title, url=url, version=version, accessed=accessed, status=status)


CISCO_DEVNET = ref(
    "Cisco DevNet · Client Delete Reasons（9800 客户端删除原因字典）",
    "https://developer.cisco.com/docs/wireless-troubleshooting-tools/client-delete-reasons",
    "IOS XE 17.x")
CISCO_LOGDOC = ref(
    "Cisco · 收集 Catalyst 9800 WLC 的记录与调试资料（show tech wireless / RA trace / 嵌入式抓包）",
    "https://www.cisco.com/c/en/us/support/docs/wireless/catalyst-9800-series-wireless-controllers/215523-quick-start-guide-on-what-logs-and-debug.html",
    "Catalyst 9800")
CISCO_TELEMETRY = ref(
    "Cisco Catalyst 9800 Configuration Guide · Streaming Telemetry 排错命令集",
    "https://www.cisco.com/c/en/us/td/docs/wireless/controller/9800/17-6/config-guide/b_wl_17_6_cg/streaming-telemetry-on-Cisco-Catalyst-9800-series-wireless-controller.html",
    "IOS XE 17.6.x")
FGT_CONN = ref(
    "Fortinet · FortiAP connection issues（CAPWAP / cw_acd / sniff 转 PCAP）",
    "https://docs.fortinet.com/document/fortiap/7.6.6/fortiwifi-and-fortiap-configuration-guide/266575",
    "FortiAP 7.6.6")
FGT_ADMIN = ref(
    "Fortinet FortiGate Administration Guide · Checking wireless information",
    "https://docs.fortinet.com/document/fortigate/7.0.0/administration-guide/541855/checking-wireless-information",
    "FortiOS 7.0.0")
FGT_CLI = ref(
    "Fortinet 中文一本通 · 常用排错命令（wireless-controller wlac / sta_filter / wtp_filter）",
    "https://support.fortinet.com.cn/troubleshooting/diagnostics_cli", "FortiOS 7.x")
FGT_KB = ref(
    "Fortinet Community · Managed FortiAP showing as offline/down",
    "https://community.fortinet.com/fortigate-3/troubleshooting-tip-managed-fortiap-showing-as-offline-down-114487",
    "FortiGate / FortiAP")
RKZ_API = ref(
    "RUCKUS · Virtual SmartZone High Scale Public API Reference Guide",
    "https://docs.ruckuswireless.com/smartzone/6.0.0/vszh-public-api-reference-guide-600.html",
    "6.0 / 7.x")
HW_LOG = ref(
    "Huawei · WLAN AC AirEngine X700 Log Reference（日志含义 / 可能原因 / 处理步骤）",
    "https://support.huawei.cn/enterprise/zh/doc/EDOC1100459361/9d0ee50f",
    "V600R025C00")
HW_TS = ref(
    "Huawei · WLAN Troubleshooting Guide (V600) · 诊断信息收集",
    "https://support.huawei.com/enterprise/en/doc/EDOC1100372040/1d67b7ce/EN-US_TOPIC_0000002450387788",
    "V600")
HW_NB = ref(
    "Huawei · iMaster NCE-Campus 北向告警接口（查询当前/历史告警）",
    "https://support.huawei.cn/enterprise/zh/doc/EDOC1100458566/ca8ec7de",
    "V300R024C10")

RULES = [
    dict(
        id="WLC-GEN-001", category="packet", scope="CAPWAP / AP 上线", vendors=["*"],
        symptom="AP 无法加入控制器，或卡在 Discovery / Join 阶段",
        evidence=["UDP 5246 / 5247 报文缺失或单向", "Discovery Request 无 Response", "DTLS 握手超时或证书校验失败", "AP 获取不到管理 IP / Option 43 或 DNS 未配置"],
        detection="按 AP MAC 建会话时间轴，检查 Discovery → Response → DTLS → Join → Configuration 五阶段是否齐全；缺哪一段就定位到该阶段",
        severity="critical", conclusion="AP 到控制器的控制通道不通，属网络层或管理访问配置问题，而非射频问题",
        remediation=["核对 AP 的 DHCP 地址与 ARP 表", "核对控制器管理接口是否放行 CAPWAP（FortiGate 为 Security Fabric Connection）", "核对 ACL / 防火墙 / 中间设备是否丢弃 UDP 5246、5247", "核对 DTLS 证书、系统时间与版本兼容"],
        doc_refs=[FGT_CONN, FGT_KB], verify_status="official-doc", priority="P1",
        note="把抓包结论落到图纸：AP 离线时该 AP 不参与热力图计算，并在图层上标注",
    ),
    dict(
        id="WLC-GEN-002", category="log", scope="AP 反复离线", vendors=["*"],
        symptom="单台或全体 AP 间歇性掉线重连",
        evidence=["控制消息达到最大重传次数", "心跳 / echo 超时", "上行交换机端口错包、PoE 掉电", "控制器守护进程崩溃日志"],
        detection="统计单位时间内的 AP down/up 事件与重连间隔；按同一上联交换机 / 同一 PoE 供电设备聚合，判断是否共因",
        severity="high", conclusion="控制面保活失败；若多 AP 同时发生且共享上行，则优先怀疑上行链路或控制器进程",
        remediation=["检查上联端口 CRC / 错包与 PoE 预算", "读取控制器 WTP 状态的 last failure 字段", "读取守护进程崩溃日志", "按官方流程重启控制面进程（注意会中断全部 AP）"],
        doc_refs=[FGT_CONN, FGT_KB], verify_status="official-doc", priority="P1",
        note="FortiGate 侧对应 diagnose wireless-controller wlac -c wtp 的 last failure : 8",
    ),
    dict(
        id="WLC-GEN-003", category="packet", scope="客户端 DHCP", vendors=["*"],
        symptom="终端已关联但拿不到 IP，或反复 169.254 地址",
        evidence=["DHCP Discover 无 Offer", "客户端在认证后被删除，原因含 DHCP_FAILURE / MN_DHCP_TIMEOUT", "VLAN 未在控制器或上联交换机创建", "地址池耗尽"],
        detection="在客户端时间轴上关联 Association → 802.1X → DHCP 四步；若 DHCP 无响应则检查 VLAN/DHCP 可达性，而非空口",
        severity="high", conclusion="地址分配链路故障，属于配置 / 网络层问题",
        remediation=["核对 WLAN/Policy 绑定的 VLAN 是否存在并放行", "核对 DHCP 池容量与中继", "核对客户端隔离 / ACL 是否误拦 DHCP"],
        doc_refs=[CISCO_DEVNET, CISCO_LOGDOC], verify_status="official-doc", priority="P1",
        note="Cisco 9800 可用 Wireshark 过滤式 ip.addr==<client-ip> || eapol || bootp 复核",
    ),
    dict(
        id="WLC-GEN-004", category="packet", scope="802.1X / EAPOL", vendors=["*"],
        symptom="终端卡在认证、反复重关联或提示密码错误",
        evidence=["EAPOL 四步握手不完整", "EAP timeout / EAP failure", "RADIUS 无响应或 Access-Reject", "服务器证书过期 / 时间不同步"],
        detection="区分「空口丢 EAPOL」与「AAA 侧拒绝」：抓包看 EAPOL 是否到达，控制器日志看 RADIUS 结果码；命中 EAP_TIMEOUT / 8021X_FAILURE 类原因码",
        severity="high", conclusion="认证链路失败，优先核查 AAA 服务器与证书，不调整射频参数",
        remediation=["核对 RADIUS 可达性、共享密钥与策略", "核对证书链有效期与设备时间 / NTP", "核对 WPA3/PMF/802.1X 组合与终端能力"],
        doc_refs=[CISCO_DEVNET], verify_status="official-doc", priority="P1",
        note="随机 MAC（iOS 14 / Android 10）会破坏基于 MAC 的 iPSK 等策略，需纳入提示",
    ),
    dict(
        id="WLC-GEN-005", category="packet", scope="Deauth / Disassoc", vendors=["*"],
        symptom="终端无规律掉线，或大量 deauth 帧",
        evidence=["802.11 reason code 分布", "低 RSSI 触发的主动踢除", "802.11w / PMF SA Query 超时", "Beacon loss / AP radio reset"],
        detection="按 reason code 聚合计数，区分「终端主动」「AP 主动」「疑似伪造」；与 AP 射频复位、信道切换事件做时间对齐",
        severity="high", conclusion="掉线原因可归因到具体 reason code 与触发方，避免笼统归为「信号差」",
        remediation=["低 RSSI 门限 / 802.11v 参数复核", "PMF 与终端兼容性复核", "检查 AP 射频复位与 DFS 信道切换频率"],
        doc_refs=[CISCO_DEVNET], verify_status="official-doc", priority="P1",
        note="reason code 字典应做成带版本范围的常量表，不随 UI 文案变化",
    ),
    dict(
        id="WLC-GEN-006", category="packet", scope="空口重传 / 隐藏节点", vendors=["*"],
        symptom="速率低、时延抖动大、重传率异常",
        evidence=["802.11 Retry 位比例高", "低 MCS / 低 RSSI / 低 SNR", "信道利用率与非 Wi-Fi 干扰抬升", "多 AP 同信道冲突"],
        detection="把重传率与 SINR 图层、干扰源位置做空间叠加；区分「覆盖不足」与「同信道竞争」两类",
        severity="medium", conclusion="链路质量问题需按空间位置归因，不能只调发射功率",
        remediation=["先按干扰引擎给信道建议", "再评估 AP 位置 / 功率 / 频段引导", "复测 RSSI、SNR、MCS 与重传率"],
        doc_refs=[CISCO_DEVNET, CISCO_TELEMETRY], verify_status="official-doc", priority="P2",
        note="与方案书第 6 节热力图、第 15 节 SSID 门限、干扰页共用同一份指标字典",
    ),
    dict(
        id="WLC-GEN-007", category="config", scope="漫游", vendors=["*"],
        symptom="跨 AP 移动时业务中断、VoWiFi 掉话、AGV 卡滞",
        evidence=["802.11k/v/r 配置不一致", "跨 SSID / 跨 VLAN 漫游后需重新 DHCP", "SA Query 超时", "重叠区 RSSI 低于门限"],
        detection="结合图纸覆盖热力图计算相邻 AP 重叠区是否达到该 SSID 的 min_rssi；叠加漫游事件与认证耗时",
        severity="high", conclusion="漫游失败多为「覆盖重叠不足 + 快速漫游参数不一致」的复合问题",
        remediation=["按 SSID 校验 k/v/r 与 WMM/DSCP 配置", "调整 AP 间距 / 功率保证重叠区", "语音与 AGV 类 SSID 单独设门限"],
        doc_refs=[], verify_status="needs-field-check", priority="P2",
        note="官方参数取值需按厂商版本文档核实后再写入规则，当前为方法学条目",
    ),
    dict(
        id="WLC-GEN-008", category="config", scope="配置漂移", vendors=["*"],
        symptom="同一园区两台控制器 / 两组 AP 表现不一致，或变更后出现新问题",
        evidence=["两次配置快照的结构化 diff", "无对应工单的变更记录", "主备控制器配置不一致"],
        detection="把配置快照归一化为「对象 → 属性」键值表后做 diff；按变更时间与故障时间轴对齐",
        severity="medium", conclusion="给出可执行的最小差异集，并标注哪些差异会改变射频或认证行为",
        remediation=["先生成变更清单与回滚清单，人工审批后再执行", "工具不自动下发配置"],
        doc_refs=[], verify_status="needs-field-check", priority="P2",
        note="与方案书第 8 节「可回滚变更清单」共用同一数据结构",
    ),
    dict(
        id="WLC-GEN-009", category="config", scope="时间 / 证书 / 许可", vendors=["*"],
        symptom="DTLS / 802.1X 突然大面积失败，或 AP 授权数受限",
        evidence=["控制器与 AP / AAA 时钟偏差", "证书过期", "许可到期或资源不足告警"],
        detection="把 NTP 状态、证书有效期、许可剩余天数作为每日基线巡检项，提前 N 天预警",
        severity="high", conclusion="基础设施类问题，常被误判为空口或终端问题",
        remediation=["核对 NTP 与证书", "核对许可资源与到期时间"],
        doc_refs=[HW_LOG], verify_status="official-doc", priority="P1",
        note="华为 Log Reference 的 hwApServiceInterruptAlertTrap 明确列出「脱管 / 许可到期 / 资源不存在」三种可能原因",
    ),
    dict(
        id="C9800-LOG-001", category="log", scope="客户端删除原因字典", vendors=["Cisco"],
        symptom="9800 上客户端反复被删除，syslog 只给出内部原因码",
        evidence=["CO_CLIENT_DELETE_REASON_* / MN_* 原因码", "RA trace 中对应事件段", "show logging profile wireless 的 always-on 记录"],
        detection="把官方 Client Delete Reasons 表编译为「原因码 → 是否正常 → 建议动作 → 需补充的证据」查表规则，附原文引用",
        severity="medium", conclusion="把不可读的内部码翻译为排障动作，并区分「正常行为」与「需介入」",
        remediation=["按原因码决定是否需要 AAA / DHCP / 空口抓包补充证据", "高频原因码按 SSID、AP、时间段聚合"],
        doc_refs=[CISCO_DEVNET, CISCO_LOGDOC], verify_status="official-doc", priority="P1",
        note="官方表明确标注 Normal 与建议动作（如 DHCP_FAILURE → 查 DHCP 并收集 RA trace）",
    ),
    dict(
        id="C9800-PKT-001", category="packet", scope="9800 证据采集", vendors=["Cisco"],
        symptom="现场有故障但缺少可用证据，事后无法复现",
        evidence=["show tech-support wireless", "debug wireless mac（Radioactive Trace）", "show logging profile wireless filter mac ... to-file always-on-", "GUI Troubleshooting > Packet Capture / monitor capture"],
        detection="按「先 always-on 历史 → 再 RA trace 复现 → 最后嵌入式抓包」的取证阶梯，避免一上来全量 debug 拖垮控制器",
        severity="info", conclusion="形成可复用的取证 SOP，并把输出文件归一化为统一证据格式",
        remediation=["复现前记录 show clock 建立时间基准", "复现后及时关闭 debug / 清除条件", "大客户端量时增大抓包缓冲（官方示例 100 MB）"],
        doc_refs=[CISCO_LOGDOC, CISCO_TELEMETRY], verify_status="official-doc", priority="P1",
        note="9800 的 always-on trace 可回溯数小时至数天，适合事后复盘",
    ),
    dict(
        id="C9800-CFG-001", category="config", scope="Profile / Tag 引用完整性", vendors=["Cisco"],
        symptom="SSID 可见但连不上、连上无 IP、或 AP 未按预期广播 WLAN",
        evidence=["WLAN Profile ↔ Policy Profile ↔ Policy Tag / Site Tag / RF Tag 引用", "AP 是否被打了正确 Tag", "Policy Profile 中的 VLAN 是否存在"],
        detection="构建配置对象的引用图，检测悬空引用、未绑定 Tag 的 AP、以及 VLAN 在控制器上不存在三类问题",
        severity="critical", conclusion="配置引用断裂，属确定性错误，可直接给出修复对象清单",
        remediation=["补齐引用或修正 Tag 绑定", "核对 VLAN 与 SVI", "修改前保存快照并生成回滚清单"],
        doc_refs=[CISCO_LOGDOC], verify_status="official-doc", priority="P1",
        note="官方排错文档把「No valid VLAN defined in policy profile」「WLAN/policy profile 变更导致断连」列为主要场景",
    ),
    dict(
        id="C9800-CFG-002", category="config", scope="AAA 下发属性一致性", vendors=["Cisco"],
        symptom="部分用户认证成功但无网络访问，或 VLAN 分配异常",
        evidence=["RADIUS 下发的 ACL 在 WLC 上不存在", "RADIUS 下发的 VLAN 在 WLC 上不存在", "AAA override 与本地策略冲突"],
        detection="读取 RADIUS 实时日志的授权属性，与控制器本地对象做存在性校验",
        severity="high", conclusion="AAA 与控制器本地配置不一致，不是认证失败",
        remediation=["在 WLC 上补齐 ACL / VLAN", "核对 AAA override 优先级"],
        doc_refs=[CISCO_LOGDOC], verify_status="official-doc", priority="P2",
        note="官方 9800 客户端连接问题文档明确列出这两类场景",
    ),
    dict(
        id="C9800-CFG-003", category="compat", scope="版本 / 型号兼容", vendors=["Cisco"],
        symptom="新装 Wi-Fi 7 AP 无法上线，或 6 GHz / MLO 不生效",
        evidence=["控制器 IOS XE 版本", "AP 型号", "型号库 min_controller_version"],
        detection="把控制器实读版本与第 14 节兼容矩阵比对，低于阈值则告警并禁用 6 GHz / MLO 图层",
        severity="high", conclusion="版本不满足，属兼容性阻断，不是配置错误",
        remediation=["按官方推荐版本升级控制器", "升级前禁用依赖新特性的规划图层"],
        doc_refs=[CISCO_TELEMETRY], verify_status="official-doc", priority="P1",
        note="Cisco 917x 系列需 IOS XE 17.15.2+；官方当前推荐 17.18.4a（见方案书 14.6）",
    ),
    dict(
        id="C9800-CFG-004", category="config", scope="NETCONF/遥测采集配置", vendors=["Cisco"],
        symptom="工具拿不到 9800 数据，或遥测订阅无数据",
        evidence=["netconf-yang 未启用 / SSH 830 未放行", "AAA 未配置本地认证", "telemetry ietf subscription 状态异常", "pubd 进程异常"],
        detection="按官方遥测排错命令清单逐项检查：show telemetry ietf subscription all / receiver / connection、show platform software yang-management process",
        severity="medium", conclusion="采集通道未就绪，需先在控制器侧开启只读能力",
        remediation=["启用 netconf-yang 与 AAA 本地登录", "核对订阅、接收者与连接状态", "必要时收集 request platform software trace archive"],
        doc_refs=[CISCO_TELEMETRY], verify_status="official-doc", priority="P2",
        note="官方提醒：9800 的 NETCONF trustpoint 只支持 RSA 密钥，EC 密钥会导致 ncsshd 崩溃",
    ),
    dict(
        id="FGT-CAPWAP-001", category="packet", scope="FortiAP 不上线", vendors=["Fortinet"],
        symptom="FortiAP 显示 offline / down",
        evidence=["get system arp / DHCP 监控中无 AP 地址", "管理接口未启用 Security Fabric Connection", "CAPWAP 5246 无响应", "cw_acd 进程异常"],
        detection="按官方五步：ARP/DHCP → WTP last failure → SSH 到 AP → sniff 5246 → wtp_filter + cw_acd debug",
        severity="critical", conclusion="逐层定位到地址分配、管理访问、控制进程或链路中的具体一层",
        remediation=["开启/扩容 DHCP", "接口启用 Security Fabric Connection", "必要时按官方流程重启 cw_acd（会短暂中断全部 AP）"],
        doc_refs=[FGT_CONN, FGT_KB, FGT_CLI], verify_status="official-doc", priority="P1",
        note="FortiGate 自 6.2 起 CAPWAP 访问归入 Security Fabric Connection；6.0.x 之前是独立选项",
    ),
    dict(
        id="FGT-LOG-001", category="log", scope="WTP 失败原因码", vendors=["Fortinet"],
        symptom="AP 显示 down 但无明确原因",
        evidence=["diagnose wireless-controller wlac -c wtp 的 last failure / last failure param / last failure time", "示例 last failure : 8 表示控制消息达到最大重传次数"],
        detection="把 last failure 码与时间戳映射到官方 KB 的原因说明，并与 AP 掉线事件时间轴对齐",
        severity="high", conclusion="把不可读数字翻译为具体失败阶段",
        remediation=["按失败阶段检查链路 / 认证 / 版本", "收集 cw_acd debug 后提交工单"],
        doc_refs=[FGT_KB, FGT_CLI], verify_status="official-doc", priority="P1",
        note="原因码表需按 FortiOS 分支维护，不同版本取值可能不同",
    ),
    dict(
        id="FGT-PKT-001", category="packet", scope="FortiGate 抓包导出", vendors=["Fortinet"],
        symptom="需要确认 CAPWAP 究竟停在哪个阶段",
        evidence=["diagnose sniff packet \"port 5246\" 6 0 l（verbose 可转 PCAP）", "AP 侧 diag_sniffer any \"port 5246 or port 5247\"", "关联成功的 Discovery / Response / DTLS / Join / Configuration 序列"],
        detection="把 PCAP 解析为阶段序列并标注缺失阶段；DTLS 响应慢或缺失时提示证书/配置方向",
        severity="info", conclusion="给出可复核的报文级证据，而不是只看 UI 状态",
        remediation=["同时从 FortiGate 与 AP 两侧抓包对比", "抓包 3–5 分钟即可停止，避免长时间开销"],
        doc_refs=[FGT_CONN, FGT_KB], verify_status="official-doc", priority="P1",
        note="官方明确给出可转换为 PCAP 的 verbose 抓包语法",
    ),
    dict(
        id="FGT-LOG-002", category="log", scope="客户端级实时 debug", vendors=["Fortinet"],
        symptom="某台终端连不上或频繁掉线，但整体指标正常",
        evidence=["diagnose wireless-controller wlac -d sta", "sta_filter <MAC> 后 IEEE 802.11 mgmt::disassoc / STA del 事件", "cw_acd 时间戳日志"],
        detection="按客户端 MAC 生成关联 → 认证 → disassoc → 删除的生命周期时间轴，定位被删除的直接原因",
        severity="high", conclusion="单终端问题定位到认证、 disassoc 或 STA 删除事件",
        remediation=["按事件原因核查终端 / SSID 安全配置", "必要时开 wtp_filter 看 AP 侧"],
        doc_refs=[FGT_ADMIN, FGT_CLI], verify_status="official-doc", priority="P1",
        note="与现有「现场连接」页的终端时间轴可直接对接，替换模拟数据",
    ),
    dict(
        id="FGT-CFG-001", category="config", scope="VAP / SSID / 接口引用", vendors=["Fortinet"],
        symptom="SSID 未广播、广播在错误 AP 组，或终端进了错误 VLAN",
        evidence=["diagnose wireless-controller wlac -c vap（MAC / BSSID / SSID / 接口名 / AP IP）", "show wireless-controller wtp / vap / wtp-profile", "接口 VLAN 与 DHCP 配置"],
        detection="构建「SSID → VAP → wtp-profile → AP 组 → 接口/VLAN」引用图，检测悬空引用与不广播该 SSID 的 AP",
        severity="high", conclusion="配置引用错误，可直接列出需修正的对象",
        remediation=["修正 VAP 与 wtp-profile 绑定", "核对接口 VLAN 与 DHCP", "把实读结果回写 SSID 模块的 AP 绑定（方案书第 15 节）"],
        doc_refs=[FGT_CLI, FGT_ADMIN], verify_status="official-doc", priority="P1",
        note="这是替换「SSID → AP 绑定」模拟数据最直接的实读来源",
    ),
    dict(
        id="FGT-CRASH-001", category="log", scope="控制面进程崩溃", vendors=["Fortinet"],
        symptom="全部 AP 同时掉线，重启后恢复一段时间又复发",
        evidence=["diagnose debug crashlog read", "cw_acd 崩溃记录", "FortiAP 侧 /var/log/messages 与 dmesg"],
        detection="把崩溃时间与全体 AP 掉线事件做相关性检查；若高相关则优先判定为软件缺陷而非网络",
        severity="critical", conclusion="需厂商工单 / 版本升级，不应通过调射频参数「修复」",
        remediation=["导出崩溃日志与配置文件", "核对官方已知问题与推荐版本", "提交工单"],
        doc_refs=[FGT_CONN, FGT_KB], verify_status="official-doc", priority="P2",
        note="官方把「controller daemon crashes → crashlog」列为 AP 连接问题的独立分支",
    ),
    dict(
        id="RKZ-API-001", category="config", scope="SmartZone API 接入", vendors=["RUCKUS"],
        symptom="REST 接口返回私有错误码，或升级后工具失效",
        evidence=["serviceTicket 获取失败 / 过期", "105 Unsupported API version", "152 备集群不允许操作", "API 版本矩阵与 SZ 大版本不匹配"],
        detection="接入前先读控制器版本并对照 API 版本矩阵；把私有错误码编译为查表规则",
        severity="medium", conclusion="采集通道或 API 版本不匹配，属工具侧适配问题",
        remediation=["改用 https://{host}:8443/wsg/api/public 前缀", "按矩阵选择 API 版本", "主集群节点执行写类操作"],
        doc_refs=[RKZ_API], verify_status="official-doc", priority="P2",
        note="官方提供设备内置 OpenAPI 文档 /wsg/apiDoc/openapi，可自动生成字段映射",
    ),
    dict(
        id="RKZ-LOG-001", category="log", scope="告警与事件码", vendors=["RUCKUS"],
        symptom="控制器告警刷屏或告警含义不明",
        evidence=["Alarm and Event Reference Guide 告警码", "SNMP Reference Guide trap", "Troubleshooting and Diagnostics 所述采集流程"],
        detection="按官方告警码表建立「码 → 级别 → 可能原因 → 建议动作」映射，并关联 AP / WLAN 对象",
        severity="medium", conclusion="把厂商告警翻译为统一事件对象，进入统一时间轴",
        remediation=["按官方诊断流程补充证据", "按告警级别与频率抑制误报"],
        doc_refs=[RKZ_API], verify_status="official-doc", priority="P2",
        note="官方每个 SZ 版本都配套 Alarm and Event / SNMP / Command / Troubleshooting 四本参考，按版本锁引用",
    ),
    dict(
        id="RKZ-CFG-001", category="compat", scope="旧 AP 与控制器版本", vendors=["RUCKUS"],
        symptom="老 AP 升级后无法注册，或新控制器纳管不了旧 AP",
        evidence=["ZD / AP 版本不一致", "9.8.3.0.58 为 7962/7762/7025 最后推荐版", "9.9 后不支持 7962/7762，9.10 后不支持 7025"],
        detection="以型号库的 lifecycle 与 min/max 版本字段做上线前校验",
        severity="high", conclusion="固件上限约束，属规划阶段阻断项",
        remediation=["冻结控制器版本或替换 AP", "在型号库中标注上限并留痕"],
        doc_refs=[], verify_status="needs-field-check", priority="P3",
        note="沿用方案书第 14.9 节老旧型号策略；具体版本上限需现场铭牌与读数复核",
    ),
    dict(
        id="HW-LOG-001", category="log", scope="日志参考规则化", vendors=["Huawei"],
        symptom="AC 上报告警但缺少处理指引",
        evidence=["WLAN Log Reference 的「日志含义 / 参数 / 可能原因 / 处理步骤」四段式结构", "示例 WLAN/4/hwApServiceInterruptAlertTrap_active（脱管 / 许可到期 / 资源不足）"],
        detection="把官方四段式直接编译为规则：命中告警 ID → 展开可能原因清单 → 按处理步骤生成核查动作",
        severity="medium", conclusion="厂商日志自带处理步骤，是官方文档融合成本最低的切入口",
        remediation=["按官方步骤逐项核查", "把「联系技术支持」类步骤标记为需外部工单"],
        doc_refs=[HW_LOG, HW_TS], verify_status="official-doc", priority="P1",
        note="建议优先拿华为 Log Reference 做「文档 → 规则」自动生成器的样板",
    ),
    dict(
        id="HW-API-001", category="config", scope="北向告警与诊断收集", vendors=["Huawei"],
        symptom="需要批量获取多站点告警与设备诊断包",
        evidence=["NCE 北向 RESTCONF /restconf/v1/data/ietf-alarms:alarms/alarm-list（分页、告警级别、时间范围）", "CampusInsight /rest/fault/v1/current-alarms + X-Auth-Token", "display diagnostic-information 一键诊断包"],
        detection="按站点/设备拉取当前与历史告警，转成统一事件对象；诊断包按设备归档并保留 7 天默认留存约束",
        severity="info", conclusion="建立可批量、可回溯的告警与诊断证据入口",
        remediation=["注意历史告警默认只查最近 3 天，需显式指定时间范围", "WAC 诊断日志不支持自动收集，需手动触发"],
        doc_refs=[HW_NB, HW_TS], verify_status="official-doc", priority="P1",
        note="官方明确：WAC 诊断日志无法自动采集，且接口有并发数 2 的限制",
    ),
]


def main():
    csv_path = BASE / "wlc-vendor-adapters.csv"
    json_path = BASE / "wlc-diagnostic-rules.json"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=VENDOR_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in VENDORS:
            w.writerow(row)

    payload = {
        "schema_version": "1.0",
        "generated_at": DATE,
        "source_note": (
            "离线规则库种子。只存索引、摘要、官方链接、适用版本与核对日期；"
            "不整篇内置厂商文档。每条结论必须可回查到 doc_refs；"
            "verify_status=needs-field-check 的条目不得直接用于自动决策。"
        ),
        "counts": {
            "vendors_rows": len(VENDORS),
            "rules": len(RULES),
            "official_doc_refs": len({r["url"] for x in RULES for r in x["doc_refs"] if r["url"]}),
            "needs_field_check": sum(1 for x in RULES if x["verify_status"] == "needs-field-check"),
        },
        "categories": ["packet", "log", "config", "compat"],
        "rules": RULES,
        "vendor_matrix_file": csv_path.name,
    }
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("vendors:", len(VENDORS), "rules:", len(RULES))
    print("doc refs:", payload["counts"]["official_doc_refs"])
    print("needs-field-check:", payload["counts"]["needs_field_check"])
    print("csv:", csv_path, csv_path.stat().st_size, "B")
    print("json:", json_path, json_path.stat().st_size, "B")


if __name__ == "__main__":
    main()
