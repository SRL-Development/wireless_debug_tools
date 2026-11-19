# -*- coding: utf-8 -*-
"""生成 WLC 离线知识包（v2.0）——全部内置，运行时零外网请求。

输入：
  vendor_docs/cisco_delete_reasons.json   从 Cisco DevNet 公开表格结构化提炼的 270 条
  wlc-diagnostic-rules.json               上一轮的方案级规则种子（作为 playbook 补充来源）
  build_wlc_kb.py 里的厂商矩阵（不强制依赖）
输出：
  wireless_app/data/wlc_offline_kb.json   打包进 exe 的离线知识包

内容分层：
  1 error_codes   官方公开码表全量内置（Cisco 270 / Ruckus 22 / 802.11 reason / FortiGate / H3C）
  2 playbooks     诊断剧本：症状话术 + 三口径沟通话术 + 分步处置 + 验证 + 升级条件
  3 doc_library   官方文献索引与中文摘要（只存索引/摘要/链接，不存全文，不发起外网请求）
  4 commands      命令速查（含风险等级）
  5 phrase_templates  通用沟通模板

重要：中文提示由官方英文 Action 自动归纳，不是逐条翻译；界面必须保留英文原文。
"""
import json
import re
import html as _html
from pathlib import Path
from collections import Counter

import wlc_kb_extra  # 扩充文献库 / 命令 / 剧本 / 话术 / SOP（独立可维护模块）

BASE = Path(__file__).resolve().parent
DATE = "2026-11-10"
OUT = BASE / "wireless_app" / "data" / "wlc_offline_kb.json"

# ============================================================ 1. 官方码表
CISCO_SRC = BASE / "vendor_docs" / "cisco_delete_reasons.json"
CISCO_HTML = BASE / "vendor_docs" / "cisco_delete_reasons_source.html"


def parse_cisco_source():
    """优先从官方公开表格的本地快照重新解析；快照不存在时退回已结构化的 JSON。
    源快照只作维护素材，不打包进 exe、不随程序分发。"""
    if not CISCO_HTML.exists():
        return json.loads(CISCO_SRC.read_text(encoding="utf-8"))["records"]
    s = CISCO_HTML.read_text(encoding="utf-8", errors="replace")
    rows = re.findall(r"<tr>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>", s, re.S)

    def clean(x):
        x = re.sub(r"<[^>]+>", "", x)
        return _html.unescape(x).replace("\r", " ").replace("\n", " ").strip()

    out = []
    for a, b, c, d in rows:
        code, sev, expl, act = map(clean, (a, b, c, d))
        if code.startswith(("CO_CLIENT_DELETE_REASON", "MN_")):
            out.append(dict(code=code, severity=sev, explanation=expl, action=act))
    return out


def classify(text):
    t = text.upper()
    rules = [
        ("地址分配", ("DHCP", "IPLEARN", "IP_LEARN", "IP UPDATE", "IP_DOWN", "STATIC IP", "IP THEFT", "ADDRESS LEARNING")),
        ("认证与密钥", ("EAP", "DOT1X", "8021X", "AAA", "RADIUS", "SAE", "PSK", "PMK", "EAPOL", "KEY_MGMT", "MIC", "CIPHER", "AKMP", "RSN", "WEBAUTH", "WEB_AUTH", "SUPPLICANT", "CREDENTIAL", "PASSWORD", "L3AUTH", "MAB", "4WAY")),
        ("漫游与移动性", ("ROAM", "FT_", "FTIE", "MDIE", "SA QUERY", "SAQUER", "SA_QUERY", "MOBILITY", "ANCHOR", "INTER_CTRL", "WNCD", "BSSID DOWN")),
        ("射频与空口", ("RSSI", "BEACON", "CHANNEL", "RADIO", "COVERAGE", "RETRY", "RETRANS", "RATE", "DRIVER", "OTA", "VHT", "HT_", "AID", "SLOT", "TX_", "SENSOR")),
        ("配置与策略", ("VLAN", "ACL", "POLICY", "PROFILE", "QOS", "TAG", "CONFIG", "WLAN ", "SSID", "SERVICE TEMPLATE", "EOGRE")),
        ("超时与老化", ("TIMEOUT", "IDLE", "SESSION", "EXPIRED", "AGE_", "QUIET", "SLEEP")),
        ("内部错误与缺陷", ("INTERNAL", "DEFECT", "CRASH", "MEMORY", "ALLOC", "NOOP", "NONE", "UNKNOWN", "DOWN", "FAILURE")),
    ]
    for name, keys in rules:
        if any(k in t for k in keys):
            return name
    return "其他"


def action_hint(act):
    a = (act or "").upper()
    if any(k in a for k in ("NONE REQUIRED", "NO ACTION", "NOTHING REQUIRED", "NONE NEEDED", "NON REQUIRED", "NO NEED", "NORMALLY, NONE", "NONE, UNLESS", "NONE, AS", "NONE, THIS", "NONE, OR")):
        return "官方判定通常无需处理；仅当高频出现或影响业务时补充证据"
    if "CONTACT" in a and ("TAC" in a or "SUPPORT" in a or "CISCO" in a):
        return "证据齐全后联系厂商支持（附 RA trace / show tech）"
    if "RA TRACE" in a and ("OTA" in a or "OVER THE AIR" in a or "EPC" in a):
        return "收集 RA trace 与空口抓包（OTA/EPC）后定位"
    if "RA TRACE" in a or "RA TRACES" in a:
        return "按客户端 MAC 收集 RA trace 复现"
    if "DHCP" in a or "IP ADDRESS" in a:
        return "核查 DHCP 服务器、地址池与客户端 IP 配置"
    if "RADIUS" in a or "AAA" in a or "SERVER" in a:
        return "核查 AAA/RADIUS 可达性、日志与下发属性"
    if "PSK" in a or "PASSWORD" in a or "CREDENTIAL" in a or "SUPLICANT" in a or "SUPPLICANT" in a:
        return "核对密钥/凭据与终端侧配置"
    if "VLAN" in a or "ACL" in a or "POLICY" in a:
        return "核对 VLAN / ACL / 策略对象是否存在且一致"
    if "SHOW TECH" in a or "AP LOG" in a or "AP DEBUG" in a or "AP DEBUGS" in a:
        return "收集 AP show tech、AP 日志与客户端 debug"
    if "OTA" in a or "OVER THE AIR" in a:
        return "补充空口抓包（OTA）"
    if "CONFIG" in a or "CHECK" in a or "VALIDATE" in a or "CONFIRM" in a:
        return "按官方提示核查对应配置项"
    return "按官方 Action 原文执行"


def load_cisco_codes():
    out = []
    for r in parse_cisco_source():
        sev = r["severity"]
        if sev == "Possible defect or config error":
            sev_zh, attention = "疑似缺陷或配置错误", 3
        elif sev == "May need validation":
            sev_zh, attention = "需验证", 2
        else:
            sev_zh, attention = "多数场景属正常", 1
        text = " ".join((r["code"], r["explanation"], r["action"]))
        out.append(dict(
            code=r["code"], severity=sev, severity_zh=sev_zh, attention=attention,
            explanation=r["explanation"], action=r["action"],
            category=classify(text), hint_zh=action_hint(r["action"]),
        ))
    return out


RUCKUS_CODES = [
    (0, "Internal server error", "服务器内部错误", "控制器内部异常，先记录时间与请求内容，复查控制器日志"),
    (101, "Bad HTTP request", "HTTP 请求非法", "核对 URI、方法与参数"),
    (102, "Bad HTTP response", "HTTP 响应异常", "核对控制器服务状态与网络链路"),
    (103, "Invalid HTTP request body", "请求体无效", "校验 JSON 结构与字段类型"),
    (104, "HTTP response body cannot be generated", "无法生成响应体", "控制器侧异常，收集日志"),
    (105, "Unsupported API version", "API 版本不支持", "按 API 版本矩阵选择版本；6.0 支持 v9_0/v9_1/v10_0"),
    (150, "Cluster not in service", "集群未在服务状态", "检查集群状态与节点角色"),
    (151, "Controller node not in service", "控制器节点未在服务状态", "检查该节点服务与健康状态"),
    (152, "Operation is not allowed because this is a standby cluster", "备集群不允许该操作", "写操作改到主集群执行；工具侧只读采集不受影响"),
    (201, "No active session", "无活动会话", "重新获取 serviceTicket"),
    (202, "Login denied", "登录被拒", "核对账号密码与来源 IP 策略"),
    (203, "Password expired", "密码已过期", "更新凭据后再采集"),
    (204, "Account is disabled", "账号被禁用", "启用只读账号或更换账号"),
    (205, "Account not found", "账号不存在", "核对账号名与域"),
    (206, "Account is locked", "账号被锁定", "解锁后重试，避免暴力重试"),
    (207, "User is not assigned to an admin user group", "用户未加入管理员组", "分配具备只读权限的管理员组"),
    (211, "Insufficient administrative privileges", "权限不足", "授予只读角色即可，不要给写权限"),
    (212, "Resource access denied", "资源访问被拒", "核对分区/域授权范围"),
    (301, "Resource cannot be found", "资源不存在", "核对 zone / AP / WLAN 的 ID"),
    (302, "Business rule violation", "违反业务规则", "核对对象状态与前置条件"),
    (303, "Illegal arguments", "参数非法", "核对必填参数与取值范围"),
    (304, "Bulk operation fail", "批量操作失败", "拆成单条重试并定位失败项"),
]

# IEEE 802.11 deauth / disassoc reason codes（Cisco 公开的 WAP 与社区文档整理）
DOT11_REASONS = [
    (0, "Reserved", "保留", "通常不应出现；检查抓包解析是否正确"),
    (1, "Unspecified reason", "未指明原因", "需结合控制器日志与 RA trace 判断"),
    (2, "Previous authentication no longer valid", "先前认证不再有效", "多为漫游/重认证后旧会话清理；高频出现需查认证配置"),
    (3, "Deauthenticated because sending STA is leaving or has left IBSS or ESS", "终端离开网络", "终端主动离开，通常正常"),
    (4, "Disassociated due to inactivity", "因空闲被解除关联", "核对 idle timeout 与终端省电策略"),
    (5, "Disassociated because AP is unable to handle all currently associated STAs", "AP 容量不足", "核对每射频/每 WLAN 最大客户端数，评估容量与负载均衡"),
    (6, "Class 2 frame received from nonauthenticated STA", "未认证终端发送 Class 2 帧", "终端状态机异常或认证未完成，收集 OTA"),
    (7, "Class 3 frame received from nonassociated STA", "未关联终端发送 Class 3 帧", "终端状态机异常或关联丢失，收集 OTA"),
    (8, "Disassociated because sending STA is leaving or has left BSS", "终端离开 BSS（常为漫游）", "漫游属正常；若伴随业务中断查 802.11k/v/r 与重叠覆盖"),
    (9, "STA requesting (re)association is not authenticated", "重关联前未完成认证", "查认证流程与超时"),
    (10, "Disassociated because Power Capability element is unacceptable", "功率能力元素不被接受", "核对终端能力与管理帧 IE"),
    (11, "Disassociated because Supported Channels element is unacceptable", "支持信道元素不被接受", "核对信道/法规域配置"),
    (12, "Disassociated due to BSS Transition Management", "BSS 过渡管理触发", "802.11v 主动引导；高频时核对 11v 与门限"),
    (13, "Invalid information element", "无效信息元素", "终端 IE 不符合标准，收集 OTA 与 RA trace"),
    (14, "Message integrity code (MIC) failure", "MIC 校验失败", "TKIP 计数器保护或攻击；查终端驱动与安全套件，避免继续用 TKIP"),
    (15, "4-Way Handshake timeout", "四次握手超时", "核对 PSK/密码、EAPOL 可达性、终端驱动；收集 EAPOL 抓包"),
    (16, "Group Key Handshake timeout", "组密钥握手超时", "核对组密钥更新周期与终端省电行为"),
    (17, "IE in 4-Way Handshake different from (Re)Association/Probe/Beacon", "四次握手 IE 不一致", "终端或 AP 的 RSN 能力协商不一致"),
    (18, "Invalid group cipher", "组播加密套件无效", "核对 WLAN 安全套件与终端能力"),
    (19, "Invalid pairwise cipher", "单播加密套件无效", "核对 WLAN 安全套件与终端能力"),
    (20, "Invalid AKMP", "AKMP 无效", "核对 802.1X/PSK/SAE/OWE 配置"),
    (21, "Unsupported RSNE version", "RSN 版本不支持", "核对 WPA2/WPA3 与终端支持情况"),
    (22, "Invalid RSNE capabilities", "RSN 能力无效", "核对 PMF/11w 等能力位"),
    (23, "IEEE 802.1X authentication failed", "802.1X 认证失败", "查 RADIUS 日志、凭据、证书与终端 supplicant"),
    (24, "Cipher suite rejected because of the security policy", "安全策略拒绝加密套件", "核对 WLAN 允许套件与终端请求套件"),
    (32, "Disassociated for unspecified QoS-related reason", "QoS 相关未指明原因", "核对 WMM/TSPEC 配置"),
    (33, "Disassociated because QAP lacks sufficient bandwidth", "QAP 带宽不足", "核对 CAC/准入控制与信道利用率"),
    (34, "Disassociated because excessive frames are not acknowledged", "大量帧未被确认（信道条件差）", "重点查覆盖、干扰、重传与隐藏节点"),
    (35, "Disassociated because QSTA is transmitting outside TXOP limits", "终端超出 TXOP 限制", "终端驱动/行为异常，收集 OTA"),
    (36, "Requested from peer QSTA as the STA is leaving the QBSS", "对端离开/复位", "通常正常"),
    (37, "Requested from peer QSTA as it does not want to use the mechanism", "对端不使用该机制", "通常正常"),
    (38, "Requested from peer QSTA as the STA received frames requiring setup", "机制未建立即收到帧", "查 QoS 机制协商"),
    (39, "Requested from peer QSTA due to timeout", "对端超时", "查链路与保活"),
    (45, "Peer STA does not support the requested cipher suite", "对端不支持请求的加密套件", "核对安全套件兼容性"),
]

# FortiGate WTP last failure：官方公开文档明确给出的只有 8；其余不臆造数字
FGT_FAILURES = [
    dict(code="8", name="Control message maximal retransmission limit reached", zh="控制消息达到最大重传次数",
         detail="ECHO_REQ / ECHO_RESP 保活未完成，控制器判定 AP 失联；常见于二层链路时延/丢包、中间交换机、接口未放行 CAPWAP、时区/版本缺陷",
         action="按官方 KB 五步排查：ARP/DHCP → last failure → SSH 到 AP → sniff 5246 → wtp_filter + cw_acd debug；不要只调大 max-retransmit 掩盖根因",
         source="Fortinet Community KB 114487 / FD40970 转述"),
]

H3C_CODES = [
    dict(code="2052", name="Device received Add Mobile failure response from upstream",
         zh="上游返回「添加移动用户失败」", detail="RADIUS 可能已通过，但 AC/本地 AAA 缺少授权方案或下发属性无效",
         action="display aaa online-fail-record 看真实失败原因；检查 domain 下 authorization 配置与 RADIUS 下发 VLAN/ACL 是否存在",
         source="H3C 知了社区（社区资料，非官方手册条文）", verify_status="community"),
    dict(code="2206", name="Client is in dynamic blacklist",
         zh="终端处于动态黑名单", detail="终端异常重试被拉黑，可能批量拒绝接入",
         action="display wlan dynamic-blacklist 核对；必要时 wlan dynamic-blacklist disable 并清理黑名单（需评估安全策略）",
         source="H3C 知了社区（社区资料，非官方手册条文）", verify_status="community"),
]

# ============================================================ 2. 官方文献索引（离线摘要）
def doc(did, vendor, title, version, url, summary, kind="官方配置/排错文档", status="public-verified"):
    return dict(id=did, vendor=vendor, title=title, version=version, url=url,
                kind=kind, captured=DATE, summary_zh=summary, verify_status=status,
                offline_copy="索引与中文摘要已内置；不内置全文、运行时不访问该链接",
                license_note="厂商公开文档，仅作事实性索引与摘要，版权归原作者")

DOCS = [
    doc("cisco-reasons", "Cisco", "DevNet · Client Delete Reasons", "Catalyst 9800 / IOS XE 17.x",
        "https://developer.cisco.com/docs/wireless-troubleshooting-tools/client-delete-reasons",
        "9800 客户端删除原因码全表（270 条）。四列：Log Identifier / Severity / Explanation / Action。Severity 分三档：Possible defect or config error（疑似缺陷或配置错误，30 条）、May need validation（需验证，126 条）、Normal in most scenarios（多数场景正常，114 条）。官方对每条都给出建议动作，例如 DHCP_FAILURE 查 DHCP 并收集 RA trace、EAP_TIMEOUT 查 AAA 并补 OTA。本工具已把全表结构化内置。"),
    doc("cisco-213970", "Cisco", "Catalyst 9800 Series Wireless Controllers – Common Client Connectivity Issues", "IOS XE 17.x",
        "https://www.cisco.com/c/en/us/support/docs/wireless/catalyst-9800-series-wireless-controllers/213970-catalyst-9800-wireless-controllers-commo.html",
        "列出 13 类常见客户端连接/断连场景：WebAuth 凭据无效、Policy Profile 无有效 VLAN、错误密码、RADIUS 下发的 ACL/VLAN 在 WLC 上不存在、WLAN/Policy Profile 变更、手动移除客户端、EAP 超时、AP 射频复位、WebAuth 超时（约 120 秒）、会话/空闲超时、SSID 间切换；其中多项官方标注为 normal behavior。给出基于 ALWAYS-ON tracing 的 11 步取证流程：show clock → show logging → show debugging → show logging profile wireless filter mac to-file always-on → clear platform condition all → debug wireless mac（默认 30 分钟）→ 复现 → no debug wireless mac → dir bootflash | inc ra_trace → internal 日志 → 清理 debug conditions。"),
    doc("cisco-215523", "Cisco", "收集 Catalyst 9800 WLC 的记录与调试资料", "Catalyst 9800",
        "https://www.cisco.com/c/en/us/support/docs/wireless/catalyst-9800-series-wireless-controllers/215523-quick-start-guide-on-what-logs-and-debug.html",
        "官方取证命令：show tech wireless 或 GUI Troubleshooting > Debug Bundle；show logging profile wireless filter mac to-file always-on-；debug wireless mac [internal]（RA trace）；dir bootflash: | i ra_trace；嵌入式抓包用 monitor capture + extended ACL 过滤 AP IP。"),
    doc("cisco-224944", "Cisco", "Deploy YANG Suite and Test XPath on Catalyst 9800 WLC", "Catalyst 9800 / YANG Suite",
        "https://www.cisco.com/c/en/us/support/docs/wireless/catalyst-9800-series-wireless-controllers/224944-deploy-yang-suite-and-test-xpath-on.html",
        "启用 netconf-yang 与 SSH 830、配置 AAA 本地认证；YANG Suite 拉取 wireless 相关 YANG 模型并生成 XPath；配合 telemetry ietf subscription + gRPC 推送状态数据。"),
    doc("cisco-telemetry", "Cisco", "Catalyst 9800 Configuration Guide · Streaming Telemetry 排错", "IOS XE 17.6.x",
        "https://www.cisco.com/c/en/us/td/docs/wireless/controller/9800/17-6/config-guide/b_wl_17_6_cg/streaming-telemetry-on-Cisco-Catalyst-9800-series-wireless-controller.html",
        "遥测故障排查命令集：show tech-support wireless、request platform software trace archive、show telemetry ietf subscription all / receiver / connection all、show platform software yang-management process（pubd）、set platform software trace ... debug 与恢复 notice。官方提示 9800 的 NETCONF trustpoint 只支持 RSA 密钥，EC 密钥会导致 ncsshd 崩溃。"),
    doc("cisco-dot11", "Cisco", "Deauthentication Reason Code Table / 802.11 Deauth Reason Codes", "WAP / WLAN 通用",
        "https://www.cisco.com/assets/sol/sb/WAP371_Emulators/WAP371_Emulator_v1-0-1-5/help/Apx_ReasonCodes2.html",
        "802.11 去认证/解除关联原因码 0–24 及 QoS 扩展码的标准含义（如 4 空闲、14 MIC 失败、15 四次握手超时、16 组密钥超时、23 802.1X 认证失败、24 安全策略拒绝加密套件、34 大量帧未被确认）。本工具已内置常用 0–45 码。"),
    doc("fgt-266575", "Fortinet", "FortiAP connection issues", "FortiAP 7.6.x / 8.0.1",
        "https://docs.fortinet.com/document/fortiap/7.6.5/fortiwifi-and-fortiap-configuration-guide/266575",
        "AP 连不上 / 单台或全体间歇掉线的官方流程：检查分配层网络、检查 AP 授权状态、重启 cw_acd（官方明确：会掉全部 AP）、diagnose debug crashlog read 查守护进程崩溃。抓包：diagnose sniff packet <intf> \"port 5246\" 4 快速看 CAPWAP，6 0 l 输出可转 PCAP。建议开启 FortiAP 的 SSH 登录、从 AP 侧验证到控制器的路由、用 wtp_filter + diagnose debug application cw_acd 0x7ff 定位失败阶段。官方给出成功关联的完整状态机：DISCOVERY_REQ/RESP → DTLS_SETUP/AUTHORIZE/CONN → JOIN_REQ → CFG_STATUS → CONFIG → DATA_CHAN_SETUP/CHECK → RUN，以及 ECHO_REQ 保活。"),
    doc("fgt-admin", "Fortinet", "FortiGate Administration Guide · Checking wireless information", "FortiOS 7.0.0",
        "https://docs.fortinet.com/document/fortigate/7.0.0/administration-guide/541855/checking-wireless-information",
        "diagnose wireless-controller wlac -d sta 查看客户端 RSSI/IP/空闲；sta_filter <MAC> 1 可按终端打印 IEEE 802.11 mgmt::disassoc、STA del、STA_CFG_REQ/RESP 等事件，用于生成单终端生命周期时间轴。"),
    doc("fgt-cli", "Fortinet", "FortiGate 中文一本通 · 常用排错命令", "FortiOS 7.x",
        "https://support.fortinet.com.cn/troubleshooting/diagnostics_cli",
        "wlac -c wtp / -d wtp 查看 AP；-c sta / -d sta 查看终端；-c vap 查看 VAP 的 MAC/BSSID/SSID/接口/广播 AP IP；sta_filter 与 wtp_filter 按终端或 AP 过滤；diagnose debug application cw_acd 0x7ff 查 AP 无法连接；diagnose debug crashlog read 查崩溃。"),
    doc("fgt-kb114487", "Fortinet", "Troubleshooting Tip: Managed FortiAP showing as offline/down", "FortiGate / FortiAP",
        "https://community.fortinet.com/fortigate-3/troubleshooting-tip-managed-fortiap-showing-as-offline-down-114487",
        "五步法：get system arp / DHCP 监控确认 AP 拿到地址 → diagnose wireless-controller wlac -c wtp 看 last failure（示例 8 = Control message maximal retransmission limit reached）→ execute ssh 到 AP → diagnose sniffer packet \"port 5246\" 6 0 l，若 FortiGate 未响应则检查接口 Administrative Access 的 Security Fabric Connection（6.2 起 CAPWAP 归入该项，6.0.x 之前是独立选项）→ wtp_filter + cw_acd debug。收尾用 diagnose debug disable / reset。"),
    doc("fgt-capwap", "Fortinet", "Improve CAPWAP stability in cloud-managed environments", "FortiAP 8.0.1",
        "https://docs.fortinet.com/document/fortiap/8.0.1/fortiwifi-and-fortiap-configuration-guide/519027",
        "config wireless-controller timers 下 keep-alive-interval（默认 0 = 等于 echo-interval）与 max-retransmit-interval（默认 0 = echo-interval 的一半）；官方建议 keep-alive 短于 echo 以更快发现断链，并用 diag wireless-controller wlac debug wtpidx 1 确认 WTP index 下发。属调优而非根因修复。", "官方配置文档"),
    doc("rkz-api6", "RUCKUS", "Virtual SmartZone High Scale Public API Reference Guide", "SZ 6.0（支持 v9_0/v9_1/v10_0）",
        "https://docs.ruckuswireless.com/smartzone/6.0.0/vszh-public-api-reference-guide-600.html",
        "REST API 公共前缀与 serviceTicket 认证（Service Ticket Logon API 返回），JSESSIONID Cookie 方式已标注 Deprecated；OpenAPI（OAS 2.0）文档在设备 /wsg/apiDoc/openapi。公开 22 条 Ruckus 私有错误码：0 内部错误、101–105 HTTP/版本类、150–152 集群类、201–212 会话与权限类、301–304 资源与参数类。注意：不同版本页面对 8443/wsg/api/public 与 7443/api/public 的 Recommend/Deprecated 标注存在渲染差异，接入前以设备实际版本的 API Reference 为准。"),
    doc("rkz-docs", "RUCKUS", "SmartZone Technical Documents（Alarm and Event / SNMP / Command / Troubleshooting）", "SZ 7.1.1 / 7.3.0",
        "https://support.ruckuswireless.com/documents",
        "每个 SmartZone 大版本都配套 Alarm and Event Reference Guide、SNMP Reference Guide、Command Reference Guide、Troubleshooting and Diagnostics、Public API Reference（vSZ-H / vSZ-E / SZ144），且 Alarm/Event 与 Command 有简体中文版。规则库应按版本锁定引用。", "官方文档目录"),
    doc("hw-log", "Huawei", "WLAN AC AirEngine X700 Log Reference", "V600R025C00",
        "https://support.huawei.cn/enterprise/zh/doc/EDOC1100459361/9d0ee50f",
        "日志条目采用「日志含义 / 日志参数 / 可能原因 / 处理步骤」四段式，最适合直接编译成诊断规则。示例 WLAN/4/hwApServiceInterruptAlertTrap_active：AP 业务可能在一段时间后中断，参数含 Type / InterruptReason / RemainTime；可能原因为 AC 从 iMaster NCE-Campus 脱管、License 到期、License 资源不存在；处理步骤含检查 AC 上线、display cloud license 看 Expire time、按需购买加载 License、否则联系技术支持。"),
    doc("hw-ts", "Huawei", "WLAN Troubleshooting Guide (V600) · 诊断信息收集", "V600",
        "https://support.huawei.com/enterprise/en/doc/EDOC1100372040/1d67b7ce/EN-US_TOPIC_0000002450387788",
        "iMaster NCE-Campus 可按 CPU/内存超阈或异常重启自动采集设备诊断日志（每 5 分钟一次，CPU/内存最多存 100 个、异常重启 50 个，默认保留 7 天）；官方明确 WAC 诊断日志不能自动收集，需手动触发。V100R025C00 起支持一键诊断日志/用户日志/诊断日志/KPI 按时间范围采集（单次最多 20 台）。"),
    doc("hw-nb", "Huawei", "iMaster NCE-Campus 北向接口指导 · 北向告警接口", "V300R024C10",
        "https://support.huawei.cn/enterprise/zh/doc/EDOC1100458566/ca8ec7de",
        "GET /restconf/v1/data/ietf-alarms:alarms/alarm-list 查询当前/历史告警；最大并发 2；分页时过滤条件须与首次一致；历史告警未指定时间范围时默认只查最近 3 天；limit 1–5000 默认 1000；支持 is-cleared / perceived-severity / resource / start-time 等参数。CampusInsight 侧另有 GET /rest/fault/v1/current-alarms + X-Auth-Token。"),
    doc("h3c-ts", "H3C", "H3C 无线产品故障处理手册 / WLAN Troubleshooting Guide (V7)", "Comware V7",
        "https://www.h3c.com/en/d_201906/1201570_294551_0.htm",
        "官方排错总则：收集故障现象、时间、配置、拓扑与端口连接、日志与诊断信息、硬件状态、已执行命令输出；变更前保存配置并定期远程备份；日志与运行状态文件可经 FTP/TFTP/USB 导出。中文版另有《无线产品故障处理手册》。", "官方手册"),
    doc("h3c-kms", "H3C", "知了社区：802.1X 失败码 2052 / 动态黑名单 2206", "社区资料",
        "https://kms.h3c.com/questions/dispcont/319373",
        "2052 = Device received Add Mobile failure response from upstream（上游返回添加移动用户失败）；建议 display aaa online-fail-record、display logbuffer level 7、display wlan client、display dot1x session，并检查 domain 下 authorization 配置及 RADIUS 下发 VLAN/ACL。2206 = Client is in dynamic blacklist；可用 display wlan dynamic-blacklist 核对。注意：社区资料，不视为官方手册条文，现场须复核。", "社区资料（非官方手册）", "community"),
    doc("aruba-docs", "HPE Aruba Networking", "Aruba Networking Technical Documentation / Central API", "AOS 8 / AOS 10",
        "https://www.arubanetworking.hpe.com/techdocs/",
        "AOS 8 以 Mobility Conductor + Mobility Gateway 为管理平面，CLI 侧 auth-tracebuf / user-table 可查认证与客户端状态；AOS 10 把管理功能迁移到 Aruba Central（云管）。具体 REST 端点、告警码与版本约束需按现场版本在官方门户核对后再编译规则，本知识包不臆造。", "官方文档门户", "needs-field-check"),
    doc("mist-docs", "Juniper / HPE Mist", "Mist API / Marvis 文档", "current",
        "https://www.juniper.net/documentation/product/us/en/mist/",
        "云管平台，客户端事件含认证、DHCP、漫游失败原因与 SLE 分类体系，可作为「官方错误分类」的参考来源；需云侧账号与外网，本离线版不接实时 API。", "官方文档门户", "needs-field-check"),
]

# ============================================================ 3. 命令速查
def cmd(vendor, scene, command, purpose, risk="只读"):
    return dict(vendor=vendor, scene=scene, command=command, purpose=purpose, risk=risk)

COMMANDS = [
    cmd("Cisco 9800", "取证", "show clock", "建立时间基准，用于把日志回溯到故障时刻"),
    cmd("Cisco 9800", "取证", "show logging", "查看 syslog 缓冲，快速判断系统健康"),
    cmd("Cisco 9800", "取证", "show debugging", "确认是否有遗留 debug condition（会增加日志量）"),
    cmd("Cisco 9800", "取证", "show logging profile wireless filter mac <MAC> to-file always-on-<FILE>", "导出指定终端的 always-on 历史跟踪"),
    cmd("Cisco 9800", "取证", "clear platform condition all", "清理全部 debug 条件（排障前/后都要做）", "影响调试状态"),
    cmd("Cisco 9800", "取证", "debug wireless mac <MAC> {monitor-time <seconds>}", "RA trace，默认 30 分钟；复现后务必停止", "需人工开启/关闭"),
    cmd("Cisco 9800", "取证", "no debug wireless mac <MAC>", "停止 RA trace 并生成 bootflash 日志文件", "需人工关闭"),
    cmd("Cisco 9800", "取证", "dir bootflash: | inc ra_trace", "找到生成的 RA trace 文件名"),
    cmd("Cisco 9800", "取证", "show logging profile wireless internal filter mac <MAC> to-file ra-internal-<FILE>", "内部日志（全级别全进程，量很大，官方建议由 TAC 协助解析）", "输出量大"),
    cmd("Cisco 9800", "取证", "show tech-support wireless", "一次性收集无线排障总包（GUI：Troubleshooting > Debug Bundle）"),
    cmd("Cisco 9800", "配置", "show running-config | section wireless", "核对 WLAN / Policy Profile / Tag 引用"),
    cmd("Cisco 9800", "配置", "show wireless tag policy detailed / show ap tag summary", "核对 AP 的 Policy/Site/RF Tag 绑定"),
    cmd("Cisco 9800", "遥测", "show telemetry ietf subscription all / show telemetry connection all", "核对遥测订阅与连接状态"),
    cmd("Cisco 9800", "遥测", "show platform software yang-management process", "确认 pubd 等进程健康"),
    cmd("Fortinet", "AP 状态", "diagnose wireless-controller wlac -c wtp", "查看 AP 状态、last failure 与失败时间"),
    cmd("Fortinet", "AP 状态", "diagnose wireless-controller wlac -c wtp | grep last", "只筛 last failure 行"),
    cmd("Fortinet", "终端", "diagnose wireless-controller wlac -d sta", "查看终端 RSSI / IP / 空闲时长"),
    cmd("Fortinet", "终端", "diagnose wireless-controller wlac sta_filter <MAC> 1", "按终端打印 disassoc / STA del 等事件"),
    cmd("Fortinet", "配置", "diagnose wireless-controller wlac -c vap", "查看 VAP 的 BSSID / SSID / 接口 / 广播 AP IP"),
    cmd("Fortinet", "配置", "show wireless-controller wtp / vap / wtp-profile", "导出无线控制器配置快照"),
    cmd("Fortinet", "抓包", "diagnose sniff packet <intf> \"port 5246\" 4", "快速确认 CAPWAP 控制通道是否有流量"),
    cmd("Fortinet", "抓包", "diagnose sniff packet <intf> \"port 5246\" 6 0 l", "verbose 输出，可转 PCAP 用 Wireshark 分析"),
    cmd("Fortinet", "抓包", "diagnose sniffer packet any \"port 5246 or port 5247\" 6 0 a", "同时抓控制与数据通道"),
    cmd("Fortinet", "debug", "diag wireless-controller wlac wtp_filter <SERIAL> 0-<IP>:5246 2", "按 AP 过滤，定位失败阶段"),
    cmd("Fortinet", "debug", "diagnose debug application cw_acd 0x7ff", "开启 CAPWAP 守护进程 debug"),
    cmd("Fortinet", "debug", "diagnose debug console timestamp enable / diagnose debug enable", "打开时间戳与 debug 输出"),
    cmd("Fortinet", "debug", "diagnose debug disable / diagnose debug reset", "排障结束必须关闭并复位 debug", "需人工收尾"),
    cmd("Fortinet", "崩溃", "diagnose debug crashlog read", "查控制面守护进程崩溃记录"),
    cmd("Fortinet", "网络", "get system arp", "确认 AP 是否拿到地址"),
    cmd("Fortinet", "网络", "execute ssh <user@AP-IP>", "从控制器侧验证到 AP 的可达性"),
    cmd("Fortinet", "调优", "config wireless-controller timers / set keep-alive-interval 15 / set max-retransmit-interval 6 / end", "CAPWAP 保活调优，属缓解不是根因修复", "配置变更，需审批"),
    cmd("RUCKUS", "接入", "POST https://{host}:8443/wsg/api/public/... (serviceTicket)", "登录取 serviceTicket 后调用只读 API（前缀以现场版本文档为准）"),
    cmd("RUCKUS", "接入", "https://{host}:8443/wsg/apiDoc/openapi", "取设备自带 OpenAPI 文档生成字段映射"),
    cmd("Huawei", "告警", "GET /restconf/v1/data/ietf-alarms:alarms/alarm-list", "NCE 北向查询当前/历史告警（并发上限 2，历史默认近 3 天）"),
    cmd("Huawei", "告警", "GET /rest/fault/v1/current-alarms (X-Auth-Token)", "CampusInsight 查询当前告警"),
    cmd("Huawei", "诊断", "display diagnostic-information", "一键诊断信息（V100R025C00 起可由 NCE 批量采集）"),
    cmd("Huawei", "许可", "display cloud license", "查看 AP License 的 Expire time"),
    cmd("H3C", "认证", "display aaa online-fail-record", "查看上线失败真实原因（含 2052 等）"),
    cmd("H3C", "无线", "display wlan ap all / display wlan client", "查看 AP 与在线终端"),
    cmd("H3C", "无线", "display wlan dynamic-blacklist", "查看动态黑名单（2206 相关）"),
    cmd("H3C", "无线", "display wlan service-template all / display wlan client statistics", "查看服务模板与关联失败统计"),
    cmd("H3C", "日志", "display logbuffer | include DENY|reject|error|warning|blacklist", "过滤拒绝/错误类日志"),
    cmd("H3C", "诊断", "probe 视图下 dis system internal wlan client history-record mac-address H-H-H", "按终端 MAC 查上线失败原因码（社区推荐）"),
    cmd("通用", "抓包", "Wireshark 过滤：ip.addr==<client-ip> || eapol || bootp", "同时看认证与 DHCP 报文"),
    cmd("通用", "抓包", "Wireshark 过滤：wlan.fc.type_subtype==0x0c || wlan.fc.type_subtype==0x0a", "只看 deauth / disassoc 帧"),
]

# ============================================================ 4. 诊断剧本（话术 + 处置）
def pb(pid, title, vendors, category, severity, symptom, keywords, conclusion, phrases,
       steps, docs, escalate="", rollback="", codes=None):
    return dict(id=pid, title=title, vendors=vendors, category=category, severity=severity,
                symptom=symptom, keywords=keywords, conclusion=conclusion,
                phrases=phrases, steps=steps, doc_refs=docs, escalate=escalate,
                rollback=rollback, related_codes=codes or [])


def P(user, engineer, vendor):
    return dict(to_user=user, to_engineer=engineer, to_vendor=vendor)


def S(*rows):
    return [dict(n=i + 1, action=a, command=c, verify=v) for i, (a, c, v) in enumerate(rows)]


PLAYBOOKS = [
    pb("PB-CAPWAP-001", "AP 无法加入控制器（CAPWAP 控制通道不通）", ["通用"], "AP 上线", "critical",
       "现场反馈「这个区域完全搜不到/连不上 Wi-Fi」，或网管里一台 AP 长期离线。",
       ["CAPWAP", "DISCOVERY_REQ", "DISCOVERY_RESP", "5246", "5247", "AP offline", "AP 离线", "down"],
       "AP 到控制器的控制通道不通，属网络层或管理访问配置问题，不是射频问题。",
       P("该区域的 AP 当前未与控制中心建立连接，我们正在核对 AP 的供电、上联端口与地址分配，恢复后会同步结果；期间请勿反复重启 AP。",
         "按 CAPWAP 五阶段（Discovery → Response → DTLS → Join → Configuration）定位首个缺失阶段；先查地址分配与管理访问，再看 DTLS，最后才怀疑控制器进程。",
         "问题描述：FortiGate/Cisco WLC 上 AP [序列号/MAC] 自 [时间] 起无法上线。已确认：AP 供电与端口状态、DHCP 地址、控制器管理接口 CAPWAP 放行、UDP 5246/5247 双向可达。附件：show tech / wlac -c wtp / CAPWAP 抓包 PCAP / 控制器版本。请协助确认失败阶段与是否存在已知缺陷。"),
       S(("确认 AP 是否拿到管理地址与 ARP 表项", "get system arp；DHCP 监控", "AP IP 存在且可 ping"),
         ("确认控制器管理接口放行 CAPWAP", "FortiGate：接口 Administrative Access > Security Fabric Connection", "接口已勾选（FortiOS 6.2 起 CAPWAP 归入此项）"),
         ("抓包确认控制通道是否双向", "diagnose sniff packet <intf> \"port 5246\" 6 0 l", "能看到 DISCOVERY_REQ 与 DISCOVERY_RESP"),
         ("按 AP 过滤定位失败阶段", "diag wireless-controller wlac wtp_filter <SERIAL> 0-<IP>:5246 2；diagnose debug application cw_acd 0x7ff", "状态机停在 DTLS_SETUP / JOIN / CONFIG 哪一段"),
         ("收尾关闭 debug", "diagnose debug disable；diagnose debug reset", "无遗留 debug")),
       ["fgt-266575", "fgt-kb114487", "fgt-cli"],
       "若五阶段齐全仍不上线，或出现守护进程崩溃日志，携带 crashlog 与配置文件开厂商 case。",
       "恢复接口管理访问配置 / 回滚最近一次控制器版本变更；不改动射频参数。",
       ["8"]),

    pb("PB-FGT-001", "FortiAP 反复离线：last failure : 8（控制消息达到最大重传次数）", ["Fortinet"], "AP 上线", "high",
       "用户说「Wi-Fi 时不时断一下，过一会儿又自己好了」，网管里 AP 间歇 down/up。",
       ["last failure : 8", "maximal retransmission", "Control message", "ECHO_REQ", "ECHO_RESP", "cw_acd"],
       "ECHO_REQ / ECHO_RESP 保活未完成，控制器判定 AP 失联。常见于二层链路时延/丢包、中间交换机、接口未放行 CAPWAP 或版本缺陷；单纯调大重传次数只会掩盖根因。",
       P("网络出现间歇性中断，我们已定位到接入点与控制器之间的链路保活异常，正在排查中间链路与端口，预计修复后不会反复出现；临时请勿反复重启设备。",
         "先查 last failure 与 ARP/DHCP，再查物理/二层链路（中间交换机、线路时延），最后才是保活参数；同时核对是否有同版本已知问题（如 FortiOS 7.4.2 与特定 EOL AP 组合）。",
         "问题描述：FortiGate [型号/FortiOS 版本] 下 FortiAP [型号/序列号] 间歇离线，diagnose wireless-controller wlac -c wtp 显示 last failure : 8 -- Control message maximal retransmission limit reached。附件：完整 wlac -c wtp 输出、双侧抓包（port 5246/5247）、拓扑（含中间交换机型号）、cw_acd debug、AP 侧日志。请确认是否与已知缺陷/版本兼容相关。"),
       S(("确认 AP 地址与 DHCP 池容量", "get system arp；DHCP 监控", "AP 稳定持有地址，池未耗尽"),
         ("读取失败原因与发生时间", "diagnose wireless-controller wlac -c wtp | grep last", "确认 last failure : 8 及时间规律"),
         ("双侧抓包看保活是否丢失", "FortiGate：diagnose sniffer packet any \"port 5246 or port 5247\" 6 0 a；AP：diag_sniffer any \"port 5246 or port 5247\" 6 0 a", "对比 ECHO_REQ / ECHO_RESP 是否成对"),
         ("排查中间二层设备与时延", "核对上联交换机端口 CRC/错包、PoE、链路时延", "定位是否由中间交换机或线路引起"),
         ("再考虑保活参数调优（缓解）", "config wireless-controller timers / set keep-alive-interval 15 / set max-retransmit-interval 6 / end", "离线频次下降；但需继续跟踪根因")),
       ["fgt-kb114487", "fgt-266575", "fgt-capwap"],
       "若伴随 cw_acd 崩溃、或升级后才出现且涉及 EOL 型号，携带 crashlog 开 case 并核对已知问题。",
       "回退 timers 配置到默认值 0；若由版本缺陷引起，按厂商建议回退 FortiOS 分支。",
       ["8"]),

    pb("PB-FGT-002", "全体 AP 同时掉线 / 控制面进程崩溃", ["Fortinet"], "控制面", "critical",
       "「整栋楼 Wi-Fi 全断了」，重启后恢复一段时间又复发。",
       ["crashlog", "cw_acd", "all AP", "全部 AP", "daemon", "restart"],
       "全体 AP 同时掉线且能自行恢复，优先怀疑控制器守护进程崩溃或软件缺陷，不应通过调射频参数「修复」。",
       P("园区无线出现整体中断，我们已确认是控制器侧服务异常，正在按厂商流程收集日志并准备版本处置；请勿逐台重启 AP，重启无法解决控制面问题。",
         "把崩溃时间与 AP 掉线事件做相关性检查；高相关即判定软件缺陷方向，导出崩溃日志与配置，核对官方已知问题与推荐版本。",
         "问题描述：[时间] 全部 [N] 台 FortiAP 同时离线，重启控制器后恢复，[X] 小时后复发。附件：diagnose debug crashlog read 输出、show wireless-controller wtp 与配置备份、FortiOS/FortiAP 版本、AP 侧 /var/log/messages 与 dmesg。请确认是否为已知缺陷及推荐版本。"),
       S(("读取崩溃日志", "diagnose debug crashlog read", "确认 cw_acd 或相关守护进程崩溃记录"),
         ("导出配置与版本信息", "show wireless-controller wtp / 配置备份；记录 FortiOS 与 FortiAP 版本", "资料齐全可供厂商分析"),
         ("按官方流程重启控制面进程（必要时）", "参考官方 KB 重启 cw_acd", "官方明确：重启会掉全部 AP，须在维护窗口执行"),
         ("核对已知问题与推荐版本", "对照官方 Release Notes / KB", "确认是否存在针对性修复版本")),
       ["fgt-266575", "fgt-cli", "fgt-kb114487"],
       "崩溃可复现或涉及业务中断，直接开 case，附 crashlog 与配置。",
       "版本升级/回退按厂商建议执行，保留原配置备份。"),

    pb("PB-C9800-001", "客户端已关联但拿不到 IP（DHCP / VLAN）", ["Cisco"], "地址分配", "high",
       "用户说「Wi-Fi 显示已连接但上不了网 / 提示无 Internet」。",
       ["CLIENT_DHCP_FAILURE", "MN_DHCP_TIMEOUT", "IPLEARN", "MN_AP_IPLEARN_TIMEOUT", "No Valid VLAN", "EXCLUDE_VLAN_FAIL", "IP_UPDATE_TIMEOUT"],
       "认证已完成，地址分配链路失败。优先核查 WLAN/Policy Profile 绑定的 VLAN 是否存在并放行、DHCP 池容量与中继，而不是调射频。",
       P("您的设备已经连上无线网络，但目前没有成功获取网络地址，我们正在核对地址分配服务与该网络对应的网段配置，修复后请重新连接一次即可。",
         "在客户端时间轴上定位 Association → 802.1X → DHCP 四步，确认首个失败点；比对官方 9800 常见场景「No Valid VLAN Defined on Policy Profile」与「VLAN Sent by RADIUS does not Exist」。",
         "问题描述：Catalyst 9800（IOS XE [版本]）上 SSID [名称] 的客户端 [MAC] 关联成功但无法获取地址，客户端删除原因码为 [CODE]。附件：RA trace、show logging profile wireless always-on 输出、WLAN/Policy Profile 配置、VLAN 与 DHCP 配置。请协助确认删除原因与推荐处置。"),
       S(("确认删除原因码", "show logging profile wireless filter mac <MAC> to-file always-on-<FILE>", "确认命中 DHCP/VLAN 类原因码"),
         ("核对 Policy Profile 的 VLAN", "show running-config | section wireless；核对 VLAN 数据库与 SVI", "VLAN 存在且已放行"),
         ("核对 RADIUS 下发的 VLAN/ACL", "RADIUS 实时日志 vs WLC 本地对象", "下发属性在 WLC 上存在"),
         ("核对 DHCP 池与中继", "DHCP 服务器日志 / 地址池余量", "Discover 有 Offer"),
         ("复测", "Wireshark：ip.addr==<client-ip> || eapol || bootp", "四步握手完整且拿到地址")),
       ["cisco-reasons", "cisco-213970", "cisco-215523"],
       "VLAN/DHCP 均正常但仍失败，附 RA trace 与 EPC 抓包开 case。",
       "恢复 Policy Profile / AAA 下发的原 VLAN 配置，保留变更前快照。",
       ["CO_CLIENT_DELETE_REASON_CLIENT_DHCP_FAILURE", "CO_CLIENT_DELETE_REASON_MN_DHCP_TIMEOUT", "CO_CLIENT_DELETE_REASON_IPLEARN_CONNECT_TIMEOUT", "CO_CLIENT_DELETE_REASON_EXCLUDE_VLAN_FAIL"]),

    pb("PB-C9800-002", "802.1X / EAP 认证失败或超时", ["Cisco"], "认证", "high",
       "用户说「输完账号密码一直转圈 / 反复弹出认证窗口」。",
       ["EAP_TIMEOUT", "8021X_FAILURE", "EAP_ID_TIMEOUT", "DOT1X_TIMEOUT", "AAA_SERVER_UNAVAILABLE", "CLIENT_CREDENTIAL_FAILURE", "EXCLUDE_DOT1X_AUTH_FAIL", "KEY_MGMT_MIC_VALIDATION"],
       "认证链路失败。区分「空口丢 EAPOL」与「AAA 侧拒绝」：抓包看 EAPOL 是否到达，控制器日志看 RADIUS 结果码。",
       P("您的账号认证没有完成，可能是凭据、证书或账号状态问题；请先确认账号密码与是否更换过设备，我们会同步核对认证服务器记录。",
         "按官方 Action：EAP_TIMEOUT → 查 AAA 并补 RA trace 与 OTA；EXCLUDE_DOT1X_AUTH_FAIL → 查凭据与 supplicant；AAA_SERVER_UNAVAILABLE → 查 WLC 与 RADIUS 可达性。",
         "问题描述：9800（IOS XE [版本]）SSID [名称] 客户端 [MAC] 802.1X 认证失败/超时，删除原因码 [CODE]。附件：RA trace、空口抓包、RADIUS Live Logs（按 Endpoint ID 过滤）、AAA 与 WLAN 配置、证书有效期。请协助定位是 supplicant、AAA 还是控制器侧问题。"),
       S(("导出 always-on 历史跟踪", "show logging profile wireless filter mac <MAC> to-file always-on-<FILE>", "确认认证阶段与原因码"),
         ("查 RADIUS 侧结果", "ISE/AAA 实时日志按终端 MAC 过滤", "区分 Access-Reject 与无响应"),
         ("核对凭据/证书/时间", "证书链有效期；控制器 NTP 与时钟", "证书有效、时间同步"),
         ("必要时复现并抓 EAPOL", "debug wireless mac <MAC>；Wireshark: eapol", "EAPOL 四步完整"),
         ("收尾", "no debug wireless mac <MAC>；clear platform condition all", "无遗留 debug")),
       ["cisco-reasons", "cisco-213970", "cisco-215523"],
       "EAPOL 完整但 AAA 无响应/拒绝且服务器侧无异常，附双侧日志开 case。",
       "恢复 AAA/WLAN 安全配置到变更前快照。",
       ["CO_CLIENT_DELETE_REASON_CLIENT_EAP_TIMEOUT_FAILURE", "CO_CLIENT_DELETE_REASON_CLIENT_8021X_FAILURE", "CO_CLIENT_DELETE_REASON_AAA_SERVER_UNAVAILABLE", "CO_CLIENT_DELETE_REASON_EXCLUDE_DOT1X_AUTH_FAIL"]),

    pb("PB-C9800-003", "RADIUS 下发的 ACL / VLAN 在 WLC 上不存在", ["Cisco"], "配置", "high",
       "「部分用户认证成功但没网 / 被分到错误网段」。",
       ["ACL Sent by RADIUS", "VLAN Sent by RADIUS", "EXCLUDE_ACL_FAIL", "EXCLUDE_VLAN_FAIL", "EXCLUDE_PUNT_ACL_FAIL", "AAA override"],
       "AAA 与控制器本地配置不一致，属于确定性配置错误，可直接列出需补齐的对象。",
       P("您的账号已通过认证，但网络权限没有正确下发，我们正在补齐该账号对应的访问策略，稍后重新连接即可。",
         "读取 RADIUS 实时日志的授权属性，与 WLC 本地 ACL/VLAN/重定向 ACL 做存在性校验；官方把这两类列为 9800 客户端连接问题的主要场景。",
         "问题描述：9800 上 RADIUS 为客户端下发 [ACL/VLAN 名称]，但 WLC 本地不存在该对象，客户端删除原因码 [CODE]。附件：RADIUS 授权属性截图/日志、WLC running-config 相关段落、RA trace。请确认推荐的对象命名与优先级。"),
       S(("取 RADIUS 下发属性", "AAA 实时日志 / Access-Accept 详情", "记录 ACL / VLAN / URL redirect ACL 名称"),
         ("在 WLC 上核对对象存在性", "show running-config | include <name>", "对象存在或确认缺失"),
         ("补齐对象或修正下发策略", "按变更流程在 WLC 创建 ACL/VLAN 或修 AAA override", "下发属性与本地对象一致"),
         ("复测同一账号", "重新关联并观察客户端状态", "认证后能正常获取地址并访问")),
       ["cisco-213970", "cisco-reasons"],
       "补齐后仍失败，附 RA trace 与 AAA 日志开 case。",
       "删除新增对象前先确认无其他业务引用；保留配置快照。"),

    pb("PB-C9800-004", "9800 取证阶梯：先看历史，再决定是否开 debug", ["Cisco"], "取证", "info",
       "「故障刚才发生了，现在查不到原因了」。",
       ["always-on", "RA trace", "debug wireless mac", "show tech wireless", "internal"],
       "9800 默认开启 ALWAYS-ON tracing，可回溯数小时至数天。应按「历史 always-on → 定向 RA trace 复现 → 嵌入式抓包 / internal 日志」逐级加码，避免一上来全量 debug 拖垮控制器。",
       P("我们正在调取设备故障时刻的系统记录，不需要您配合复现；如需复现会另行约定时间。",
         "先 show clock 建时间基准 → show logging → show debugging（有遗留 condition 先清）→ always-on 过滤 MAC → 不够再 clear platform condition all + debug wireless mac 复现 → 仍不足才 internal 日志或抓包。",
         "问题描述：[时间] 客户端 [MAC] 在 SSID [名称] 出现 [症状]。附件：show clock、show logging、always-on 跟踪文件、RA trace 文件名与内容、show tech-support wireless。已在排障后执行 clear platform condition all。请协助解析跟踪中的关键事件。"),
       S(("建立时间基准", "show clock", "记录控制器当前时间"),
         ("收集 syslog 与遗留 debug", "show logging；show debugging", "必要时 clear platform condition all"),
         ("导出 always-on 跟踪", "show logging profile wireless filter mac <MAC> to-file always-on-<FILE>", "文件生成并可 more/copy 导出"),
         ("复现并生成 RA trace", "debug wireless mac <MAC>（默认 30 分钟）→ 复现 → no debug wireless mac <MAC> → dir bootflash: | inc ra_trace", "得到 ra_trace 文件"),
         ("仍不足才上 internal / 抓包", "show logging profile wireless internal ...；GUI Troubleshooting > Packet Capture", "官方提示 internal 日志量很大，建议由 TAC 协助")),
       ["cisco-213970", "cisco-215523", "cisco-telemetry"],
       "internal 日志解析困难时附全部文件联系 Cisco TAC。",
       "排障结束清理 debug 条件与抓包点。"),

    pb("PB-C9800-005", "低 RSSI 被踢 / 信道条件差导致掉线", ["Cisco"], "射频", "medium",
       "「走到某个角落就断，回来又自动连上」。",
       ["DOT11_POOR_CHANNEL_CONDITIONS", "CLIENT_BEACON_LOSS", "Low RSSI", "802.11v", "RSSI threshold"],
       "终端 RSSI 低于配置的接入门限，或 AP beacon 丢失。可能是有意的低 RSSI 剔除策略，也可能是真实覆盖盲区；需结合图纸热力图与该 SSID 的 min_rssi 判断。",
       P("该位置的无线信号偏弱，终端会自动切换到更近的接入点；我们正在评估该区域的覆盖，必要时补充或调整接入点位置。",
         "核对 802.11v BSS Transition 与 Low RSSI threshold 设置；用热力图按当前 SSID 门限计算重叠区，区分「策略性剔除」与「覆盖不足」；同时看 AP 是否有射频复位。",
         "问题描述：9800 上客户端在 [位置] 频繁因 DOT11_POOR_CHANNEL_CONDITIONS / CLIENT_BEACON_LOSS 被删除。附件：RA trace、AP 日志、低 RSSI 门限与 11v 配置、现场 RSSI 打点。请确认是否为预期策略行为。"),
       S(("确认原因码与频次", "always-on 跟踪按 MAC 过滤", "统计单位时间发生次数"),
         ("核对门限配置", "WLAN/射频配置中的 Low RSSI threshold、802.11v", "门限是否过严"),
         ("按 SSID 计算覆盖与重叠区", "工具热力图（当前 SSID 的 min_rssi / 频段 / AP 绑定）", "该位置是否低于门限"),
         ("核对 AP 射频复位/信道切换", "AP 日志；MN_CHANNEL_SWITCH_AT_AP 频次（官方提示每天每射频 >4 次需关注）", "排除 AP 侧事件"),
         ("决定处置", "调整门限 / 补点 / 调整功率（先仿真再变更）", "复测 RSSI 与掉线次数")),
       ["cisco-reasons", "cisco-213970"],
       "门限与覆盖均正常但持续发生，附 OTA 抓包开 case。",
       "恢复原门限与功率配置，保留前后对比。"),

    pb("PB-C9800-006", "配置变更 / 射频复位 / 超时类断连（官方判定为正常行为）", ["Cisco"], "误报抑制", "low",
       "「刚才大家都断了一下，是不是网络故障？」",
       ["WLAN_CHANGE", "BSSID_DOWN", "AP_UPGRADE", "SESSION_TIMEOUT", "IDLE_TIMEOUT", "AP_RADIO_RESET", "MN_CHANNEL_SWITCH_AT_AP", "normal behavior"],
       "官方把 WLAN/Policy Profile 变更、AP 升级、AP 射频复位、会话/空闲超时、SSID 切换、手动移除客户端等标注为 normal behavior。这类事件应先归入「预期行为」，避免当成故障反复排查。",
       P("刚才是一次计划内的配置/设备切换，连接会自动恢复，无需您做任何操作；若仍无法使用请告知具体位置。",
         "先看是否与变更窗口、AP 升级、会话超时对齐；命中官方 Normal 类原因码时把结论标为「预期行为」，只有高频发生或影响业务才升级为问题。",
         "（通常无需提单）如确需确认：问题描述为某时段批量客户端删除，附原因码分布、变更记录与 AP 升级日志，请确认是否为预期行为。"),
       S(("统计原因码分布", "always-on / syslog 按时间段聚合", "确认是否集中在 Normal 类"),
         ("对齐变更与升级时间轴", "变更工单、AP 升级记录", "时间是否吻合"),
         ("评估频次", "统计每终端/每 AP 发生次数", "是否超出官方「高频」提示"),
         ("结论归类", "标为预期行为 / 需跟进", "避免误报")),
       ["cisco-reasons", "cisco-213970"],
       "同一 AP 每天信道切换超过 4 次、或 Normal 码异常高频时再开 case。",
       "无需回滚；如为变更引起，按变更单回滚。"),

    pb("PB-DOT11-001", "四次握手 / 组密钥超时（reason 15、16）", ["通用"], "认证", "high",
       "「密码明明对了，但一直连不上或连上就掉」。",
       ["reason 15", "reason=15", "4-Way Handshake timeout", "reason 16", "Group Key Handshake timeout", "EAPOL M1", "4WAY_HANDSHAKE_FAILED"],
       "EAPOL 四次握手未完成的典型表现：PSK 错误、终端 supplicant 异常、EAPOL 帧丢失或加密套件不兼容。",
       P("设备与安全校验没有完成，常见于密码或终端系统兼容性问题；请先忘记该网络后重新输入密码，若仍失败我们会在设备侧进一步核查。",
         "过滤 EAPOL 报文确认 M1–M4 是否完整；核对 WLAN 安全套件（WPA2/WPA3/SAE/PMF）与终端能力；PSK 场景先核对密钥，802.1X 场景查 supplicant 与 AAA。",
         "问题描述：客户端 [MAC] 在 SSID [名称] 关联后出现 802.11 reason 15/16，四次握手未完成。附件：空口 EAPOL 抓包、控制器 RA trace、WLAN 安全配置、终端型号与系统版本。请协助判断是终端侧还是网络侧。"),
       S(("抓 EAPOL 握手", "Wireshark: eapol", "确认 M1–M4 是否齐全"),
         ("核对 PSK / 安全套件", "WLAN 配置；终端侧忘记网络重配", "密码一致、套件受支持"),
         ("核对 PMF/11w 与 WPA3 兼容", "WLAN 安全配置 vs 终端能力", "能力位一致"),
         ("必要时换终端交叉验证", "同 SSID 换一台终端测试", "判定个体还是共性问题")),
       ["cisco-dot11", "cisco-reasons"],
       "多型号终端均失败且 EAPOL 完整，附双侧日志开 case。",
       "恢复 WLAN 安全套件到变更前配置。"),

    pb("PB-DOT11-002", "802.1X 认证失败（reason 23）/ 安全策略拒绝套件（reason 24）", ["通用"], "认证", "high",
       "「公司网络连不上，提示认证失败」。",
       ["reason 23", "reason=23", "802.1X authentication failed", "reason 24", "Cipher suite rejected", "Access-Reject"],
       "认证被明确拒绝或安全套件不匹配，重点在 AAA 与 WLAN 安全配置，不在信号强度。",
       P("账号认证未通过，请确认账号状态与密码；如果其他设备可以正常连接，可能是这台设备的证书或系统配置需要更新。",
         "查 RADIUS Access-Reject 的具体原因、证书有效期、终端 supplicant；再核对 WLAN 允许的加密套件与终端请求套件是否一致。",
         "问题描述：客户端 [MAC] 出现 802.11 reason 23/24。附件：RADIUS 实时日志、空口抓包、WLAN 安全配置、证书链信息。请确认拒绝原因。"),
       S(("查 AAA 拒绝原因", "RADIUS Live Logs 按 Endpoint ID 过滤", "明确 Reject 原因码"),
         ("核对证书与时间", "服务器证书有效期、设备 NTP", "证书有效、时间同步"),
         ("核对加密套件", "WLAN 安全策略 vs 终端请求 IE", "套件匹配"),
         ("复测", "重新关联", "认证成功")),
       ["cisco-dot11", "cisco-213970"],
       "AAA 显示成功但空口仍 reason 23，附双侧证据开 case。",
       "恢复 WLAN 安全策略原配置。"),

    pb("PB-DOT11-003", "MIC 失败（reason 14）与重传/信道质量差（reason 34）", ["通用"], "射频/安全", "medium",
       "「网络会突然全部断一下」或「明明满格却很卡」。",
       ["reason 14", "MIC failure", "TKIP", "reason 34", "excessive frames", "retry", "重传"],
       "reason 14 常与 TKIP MIC 计数器保护或异常帧有关；reason 34 指向信道条件差、大量帧未被确认。两者都不要靠加大发射功率解决。",
       P("该区域存在无线干扰或终端兼容性问题，我们正在做信道与干扰核查，期间可先靠近接入点使用。",
         "reason 14：停止使用 TKIP、核对终端驱动与安全套件；reason 34：结合 SINR 图层、信道利用率、重传率与干扰源位置定位是覆盖不足还是同信道竞争。",
         "问题描述：[区域] 客户端出现 reason 14/34，伴随 [重传率/速率] 异常。附件：空口抓包、信道利用率、AP 邻居表、干扰扫描结果。请协助判断是否存在已知终端兼容问题。"),
       S(("确认原因码与影响范围", "抓包 / 控制器日志按区域聚合", "单终端还是整片区域"),
         ("核对安全套件", "WLAN 是否仍启用 TKIP / 混合模式", "优先改用 AES-CCMP/WPA2/WPA3"),
         ("查信道与干扰", "工具干扰分析 + SINR 图层", "定位同信道冲突与非 Wi-Fi 干扰"),
         ("先出信道建议再动功率/位置", "贪心图着色信道建议 → 仿真对比 → 变更单", "复测重传率与速率")),
       ["cisco-dot11", "cisco-reasons"],
       "更换套件与信道后仍出现 MIC 失败，附 OTA 开厂商 case。",
       "恢复原信道/功率/安全配置，保留前后仿真对比。"),

    pb("PB-RKZ-001", "SmartZone API 接入失败 / 私有错误码", ["RUCKUS"], "采集", "medium",
       "「工具连不上控制器」「升级后采集全断了」。",
       ["Unsupported API version", "serviceTicket", "standby cluster", "No active session", "Login denied", "Insufficient administrative privileges", "105", "152", "201", "211"],
       "多为工具侧适配或账号权限问题，不是无线网络故障。按官方错误码表定位：105 版本不支持、150–152 集群状态、201–212 会话与权限、301–304 资源与参数。",
       P("这是运维平台与控制器之间的对接问题，不影响用户上网，我们正在修正采集配置。",
         "接入前先读控制器版本并对照 API 版本矩阵（6.0 支持 v9_0/v9_1/v10_0）；用 serviceTicket 而非已废弃的 JSESSIONID；只读账号即可，不给写权限；152 表示当前是备集群。",
         "问题描述：SmartZone [版本] 调用 [API] 返回 Ruckus 私有错误码 [code] -- [message]。附件：请求 URI（脱敏）、serviceTicket 获取流程、控制器版本与集群角色、设备自带 OpenAPI 文档片段。请确认该版本的推荐前缀与 API 版本。"),
       S(("确认控制器版本与集群角色", "控制器关于页 / 系统状态", "主集群、版本明确"),
         ("按矩阵选 API 版本", "对照官方 Version Matrix", "版本在支持列表内"),
         ("重新获取 serviceTicket", "Service Ticket Logon API", "ticket 有效且未过期"),
         ("核对只读账号权限", "账号所属管理员组、是否被锁定/过期", "201–212 类错误消除"),
         ("核对 URI 前缀", "以设备实际版本 API Reference 为准（8443/wsg/api/public 与 7443/api/public 标注在不同版本页面存在渲染差异）", "请求返回 2xx")),
       ["rkz-api6", "rkz-docs"],
       "版本矩阵与账号均正确仍失败，附 OpenAPI 片段与响应体开 case。",
       "回退到上一版 API 版本或改为 SNMP v2c 兜底采集。",
       ["105", "150", "151", "152", "201", "211"]),

    pb("PB-HW-001", "华为 WLAN 告警：AP 业务可能中断（脱管 / License）", ["Huawei"], "许可/平台", "high",
       "「网管提示 AP 服务将在 N 天后中断」。",
       ["hwApServiceInterruptAlertTrap", "InterruptReason", "RemainTime", "License", "display cloud license", "脱管"],
       "官方日志给出三种可能原因：AC 从 iMaster NCE-Campus 脱管、License 到期、License 资源不存在。属于基础设施类问题，常被误判为空口故障。",
       P("系统检测到无线控制器的授权或管理平台连接存在到期风险，我们正在处理，不会影响当前已连接的终端；处理完成后会同步结果。",
         "按官方处理步骤：先确认 AC 与 NCE 通信且在线；再 display cloud license 看对应 AP 类型的 Expire time（若为「-」表示资源不存在）；最后判断是否需要购买/加载 License，仍无法定位再联系技术支持。",
         "问题描述：AirEngine WAC [型号/版本] 上报 WLAN/4/hwApServiceInterruptAlertTrap_active，InterruptReason=[值]，RemainTime=[N] 天。附件：完整告警、display cloud license 输出、AC 与 NCE 在线状态、License 文件信息。请确认原因与处理建议。"),
       S(("确认 AC 与 NCE 在线", "NCE 设备列表 / AC 注册状态", "AC 在线"),
         ("检查 License 到期时间", "display cloud license", "Expire time 明确或为「-」"),
         ("按需加载/购买 License", "按官方流程在 NCE 加载", "告警消除且 RemainTime 恢复"),
         ("记录处置", "登记告警 ID、处理步骤与结果", "可回溯")),
       ["hw-log", "hw-ts"],
       "License 在有效期内且 AC 在线仍告警，携带完整日志开 case。",
       "无需回滚配置；保留 License 变更记录。"),

    pb("PB-HW-002", "华为 NCE / CampusInsight 告警与诊断包采集", ["Huawei"], "取证", "info",
       "「需要批量拉多站点的告警和设备日志」。",
       ["ietf-alarms", "current-alarms", "X-Auth-Token", "diagnostic-information", "一次采集"],
       "NCE 北向 RESTCONF 与 CampusInsight REST 提供批量告警；诊断信息可一键采集。注意官方约束：历史告警默认只查最近 3 天、接口并发上限 2、WAC 诊断日志不能自动收集。",
       P("我们正在汇总各站点的告警与设备日志用于分析，不需要现场配合操作。",
         "按站点/设备拉取当前与历史告警，转成统一事件对象；诊断包按设备归档并遵循默认 7 天留存策略；WAC 诊断日志需手动触发收集任务。",
         "问题描述：需批量获取 [站点范围] 在 [时间范围] 内的告警与诊断信息。附件：接口响应、任务 ID、采集结果列表。请确认是否存在接口限制或字段差异。"),
       S(("查询当前/历史告警", "GET /restconf/v1/data/ietf-alarms:alarms/alarm-list（指定 start-time / limit）", "返回告警列表且分页条件一致"),
         ("CampusInsight 侧补充", "GET /rest/fault/v1/current-alarms + X-Auth-Token", "当前告警补齐"),
         ("手动触发诊断信息收集", "NCE > 网络维护 > 诊断工具 > 诊断信息收集（WAC 不支持自动）", "任务成功并下载日志"),
         ("归档", "按设备 + 时间命名，记录留存期", "可回溯")),
       ["hw-nb", "hw-ts"],
       "接口返回异常或任务失败（证书过期、磁盘不足、文件服务器不可达），附任务失败原因开 case。",
       "停止采集任务，避免占满磁盘。"),

    pb("PB-H3C-001", "H3C 802.1X 上线失败：2052 / 授权下发失败", ["H3C"], "认证", "high",
       "「账号认证过了，但就是上不了线」。",
       ["2052", "Add Mobile failure", "online-fail-record", "authorization", "domain system", "203.1X"],
       "AP 把终端信息发给 AC/上游后返回「添加移动用户失败」：多为 domain 下缺少 authorization 方案，或 RADIUS 下发的 VLAN/ACL 在 AC 上不存在。社区资料，现场须复核。",
       P("账号认证已通过，但网络授权没有生效，我们正在补齐该账号对应的授权策略，稍后重新连接即可。",
         "display aaa online-fail-record 看真实失败原因；display logbuffer level 7；核对 domain 下 authorization lan-access 配置与 RADIUS 下发属性。",
         "问题描述：H3C AC [型号/版本] 上客户端 [MAC] 上线失败，日志 code=2052。附件：display aaa online-fail-record、display logbuffer、domain 与 RADIUS 配置、服务模板配置。请确认授权方案与下发属性要求。"),
       S(("查看上线失败记录", "display aaa online-fail-record", "读到 Online failure reason"),
         ("查看详细日志", "display logbuffer level 7（或 include 2052）", "定位触发模块"),
         ("核对 domain 授权方案", "检查 authentication / authorization lan-access 配置", "授权方案指向有效 radius-scheme 或 local"),
         ("核对下发属性", "RADIUS 侧 VLAN/ACL/用户组 vs AC 本地对象", "对象存在"),
         ("复测", "display wlan client", "终端上线")),
       ["h3c-kms", "h3c-ts"],
       "授权配置正确仍失败，附完整日志与配置开 case。",
       "恢复 domain 授权配置到变更前备份。"),

    pb("PB-H3C-002", "H3C 终端被动态黑名单批量拒绝（2206）", ["H3C"], "安全策略", "high",
       "「能搜到 SSID，但所有终端都连不上」。",
       ["2206", "dynamic blacklist", "动态黑名单", "Client is in dynamic blacklist", "quiet"],
       "终端异常重试触发动态黑名单，可能批量拒绝接入。关闭黑名单属安全策略变更，须评估后执行。",
       P("无线接入控制策略误将部分终端临时拦截，我们正在清理拦截状态，稍后请重新连接。",
         "display wlan dynamic-blacklist 核对是否有大量 MAC；同时排除 WLAN 服务进程异常、会话表满、CAPWAP 隧道假在线等共因；关闭黑名单前评估安全策略。",
         "问题描述：H3C AC [型号/版本] 出现批量接入拒绝，日志含 code 2206 / Client is in dynamic blacklist。附件：display wlan dynamic-blacklist、display wlan client statistics、display logbuffer、当前版本。请确认是否与已知版本问题相关。"),
       S(("确认黑名单规模", "display wlan dynamic-blacklist", "是否大量 MAC 被拉黑"),
         ("排除其他共因", "display wlan ap all / display wlan client / display process | include wlan", "服务进程与表项正常"),
         ("评估后清理", "reset wlan dynamic-blacklist all（必要时 wlan dynamic-blacklist disable）", "终端可接入；记录安全策略变更"),
         ("复测并观察 24–48 小时", "持续看拒绝统计", "不再复发")),
       ["h3c-kms", "h3c-ts"],
       "反复出现且升级后才发生，附版本信息与统计开 case，评估回退稳定版本。",
       "恢复动态黑名单原开关与静默时间配置。"),

    pb("PB-GEN-001", "版本 / 固件兼容不满足（新 AP 上不了线、6 GHz 不生效）", ["通用", "Cisco", "RUCKUS"], "兼容性", "high",
       "「新买的 Wi-Fi 7 AP 装上去没反应 / 6 GHz 搜不到」。",
       ["17.15.2", "min_controller_version", "EOL", "不支持", "固件", "版本不兼容", "compat"],
       "控制器软件版本低于该 AP 型号的最低要求，属兼容性阻断，不是配置错误也不是射频问题。",
       P("新设备需要配套的控制器软件版本，我们正在安排版本升级计划，升级完成前该批设备暂不提供服务。",
         "把控制器实读版本与型号库 min_controller_version / lifecycle 比对；低于阈值则告警并禁用 6 GHz / MLO 等依赖新特性的规划图层；老旧平台还需核对固件上限（如 ZoneDirector 9.9/9.10 之后不再支持部分老 AP）。",
         "问题描述：控制器 [型号/版本] 纳管 AP [型号] 失败，型号库标注最低版本 [X]。附件：控制器版本输出、AP 型号与序列号、兼容性矩阵引用。请确认该组合的支持状态与推荐版本。"),
       S(("读取控制器实读版本", "show version / 关于页 / API", "版本明确"),
         ("查型号库兼容矩阵", "工具设备型号库的 min_controller_version / lifecycle", "命中不满足项"),
         ("禁用依赖新特性的图层", "6 GHz / MLO 相关规划图层", "不产生误导性结论"),
         ("按官方推荐版本升级", "核对官方当前推荐版本（如 Cisco 9800 17.18.4a）", "升级后在维护窗口复测")),
       ["cisco-telemetry", "rkz-docs"],
       "升级后仍不兼容，附版本输出与矩阵引用开 case。",
       "版本回退按厂商建议执行；保留原版本镜像。"),

    pb("PB-GEN-002", "证书 / NTP / 许可到期引发大面积认证或 DTLS 失败", ["通用"], "基础设施", "critical",
       "「昨天还好好的，今天所有人突然都连不上」。",
       ["certificate", "证书", "expired", "NTP", "时间不同步", "license", "许可", "DTLS", "EAP-TLS"],
       "证书过期或控制器/AP/AAA 时钟偏差会让 DTLS 与 EAP-TLS 同时失败，常被误判为空口或终端问题。应作为每日基线巡检项。",
       P("无线认证服务出现整体性异常，我们正在恢复证书与时间同步服务，恢复后会统一通知，请暂勿反复重装配置。",
         "把 NTP 状态、证书有效期、许可剩余天数做基线巡检；华为 Log Reference 的 hwApServiceInterruptAlertTrap 已把「脱管 / 许可到期 / 资源不存在」列为标准可能原因。",
         "问题描述：[时间] 起全网 [N] 个终端认证/DTLS 失败。附件：控制器与 AAA 时间、证书有效期、License 状态、相关日志。请确认是否存在已知证书/时间相关问题。"),
       S(("核对时间同步", "show clock / NTP 状态（控制器、AP、AAA）", "时钟偏差在允许范围"),
         ("核对证书有效期", "RADIUS/EAP-TLS 服务器证书、控制器证书", "证书链未过期"),
         ("核对许可", "display cloud license 或对应厂商许可命令", "许可有效且资源充足"),
         ("复测", "挑选一台终端重新认证", "认证恢复")),
       ["hw-log", "fgt-266575"],
       "证书与时间均正常仍大面积失败，附双侧日志开 case。",
       "恢复原证书/时间配置，保留变更前备份。"),

    pb("PB-GEN-003", "配置引用完整性：Profile / Tag / VAP / VLAN 链路断裂", ["Cisco", "Fortinet", "通用"], "配置", "high",
       "「SSID 能搜到但连不上」「某个区域根本不广播这个 SSID」。",
       ["Policy Profile", "Policy Tag", "Site Tag", "VAP", "wtp-profile", "service-template", "No Valid VLAN", "引用"],
       "配置对象之间的引用断裂是确定性错误，可直接列出需修复的对象清单；同时可为「SSID → AP 绑定」提供实读来源，替代工具内的模拟配置。",
       P("该网络的配置存在未生效的绑定项，我们正在补齐配置关联，完成后该区域的信号与接入会恢复正常。",
         "构建「对象 → 属性 → 引用」图：Cisco 9800 的 WLAN Profile ↔ Policy Profile ↔ Policy/Site/RF Tag ↔ AP；FortiGate 的 SSID → VAP → wtp-profile → AP 组 → 接口/VLAN；检测悬空引用与未绑定对象。",
         "问题描述：[平台/版本] 上 SSID [名称] 未按预期广播/接入，疑似 [对象类型] 引用缺失。附件：完整配置快照、AP 组与 Tag 绑定、VLAN 与接口配置。请确认推荐的绑定方式。"),
       S(("导出只读配置快照", "show running-config / wlac -c vap / show wireless-controller wtp", "快照完整"),
         ("构建引用图并跑存在性校验", "工具配置检查（对象 → 引用）", "列出悬空引用清单"),
         ("生成变更清单与回滚清单", "只生成，不自动下发", "人工审批后执行"),
         ("按当前 SSID 复核广播 AP 集合", "实读 VAP / AP 组 / Tag 绑定", "与工具内 SSID 绑定一致"),
         ("复测", "现场走测 + 热力图重算", "覆盖与接入一致")),
       ["cisco-213970", "fgt-cli", "fgt-admin"],
       "引用完整但现象仍在，附配置快照与实读结果开 case。",
       "按回滚清单逐条恢复，保留变更前后 diff。"),

    pb("PB-GEN-004", "配置漂移 / 无工单变更引发新问题", ["通用"], "变更管理", "medium",
       "「两台控制器表现不一致」「改完配置就出问题了」。",
       ["diff", "配置漂移", "running-config", "备份", "变更", "主备不一致"],
       "把配置快照归一化后做结构化 diff，按最小差异集定位「谁在故障前改了什么」；工具不自动下发配置。",
       P("我们正在比对设备配置的历史差异以定位变更点，期间会保持现有配置不做进一步改动。",
         "定时保存只读配置快照 → 归一化为键值表 → diff → 与故障时间轴/变更工单对齐 → 输出最小差异集与回滚清单。",
         "问题描述：[时间] 后出现 [症状]，配置快照 diff 显示 [对象/属性] 变更。附件：变更前后配置快照、diff 结果、时间轴。请确认该变更是否会影响 [功能]。"),
       S(("保存/读取两次只读快照", "配置备份导出", "两份快照可比"),
         ("归一化并 diff", "对象 → 属性键值表对比", "得到最小差异集"),
         ("与故障时间轴对齐", "事件流 / 变更工单", "确认时间相关性"),
         ("生成回滚清单", "先仿真评估影响", "人工审批后执行")),
       ["h3c-ts", "cisco-213970"],
       "差异无法解释现象，附快照与日志开 case。",
       "按回滚清单恢复到变更前配置。"),

    pb("PB-GEN-005", "漫游失败：VoWiFi 掉话 / AGV 卡滞", ["通用"], "漫游", "high",
       "「走到楼梯口语音就断」「AGV 经过某段会停」。",
       ["802.11k", "802.11v", "802.11r", "FT", "SA_QUERY_TIMEOUT", "roaming", "漫游", "重叠区", "跨 VLAN"],
       "多为「覆盖重叠不足 + 快速漫游参数不一致」的复合问题；语音与 AGV 类 SSID 需单独设门限并校验 k/v/r 与 WMM/DSCP。",
       P("移动过程中的切换存在短暂中断，我们正在优化该路径上的接入点重叠与切换参数，完成后会安排复测。",
         "结合图纸热力图按当前 SSID 的 min_rssi 计算相邻 AP 重叠区；叠加漫游事件与认证耗时；核对 802.11k/v/r、PMF SA Query、跨 VLAN 三层漫游配置。",
         "问题描述：SSID [名称] 的终端 [MAC] 在 [位置] 漫游时业务中断 [X] 秒，日志含 [FT/SA query/reassoc 相关原因码]。附件：RA trace、漫游事件、k/v/r 与 WLAN 配置、现场 RSSI 走测。请确认推荐参数组合。"),
       S(("按 SSID 计算重叠区", "工具热力图（min_rssi / 频段 / AP 绑定）", "重叠区是否达标"),
         ("核对快速漫游参数", "802.11k/v/r、PMF/11w、WMM/DSCP", "配置一致且终端支持"),
         ("核对跨 VLAN / 三层漫游", "VLAN 与 mobility 配置", "漫游后是否需要重新 DHCP"),
         ("先仿真再变更", "调整 AP 间距/功率的仿真对比", "输出变更单与回滚清单")),
       ["cisco-reasons", "cisco-213970"],
       "参数与覆盖均正常仍掉话，附 OTA + RA trace 开 case。",
       "恢复原漫游参数与功率配置。"),

    pb("PB-GEN-006", "Deauth / Disassoc 风暴与 reason code 归因", ["通用"], "空口", "high",
       "「终端无规律掉线」「同一区域多人同时断」。",
       ["deauth", "disassoc", "reason code", "PMF", "802.11w", "SA Query", "beacon loss"],
       "按 reason code 聚合计数，区分「终端主动」「AP 主动」「疑似伪造」；与 AP 射频复位、DFS 信道切换事件做时间对齐，避免笼统归为「信号差」。",
       P("该区域出现短时断连，我们正在核对断连原因与接入点状态，确认后会同步处置结果。",
         "统计 reason code 分布与发起方；命中 4/8 多为正常，15/16/23 走认证方向，14 走安全套件，34 走信道质量；高频 deauth 且来源可疑时考虑 PMF/11w 与伪冒。",
         "问题描述：[区域/时间] 出现大量 deauth/disassoc，reason code 分布为 [列表]，instigator=[值]。附件：空口抓包、控制器日志、AP 射频与信道切换记录。请协助判断是否存在伪冒或已知缺陷。"),
       S(("抓 deauth/disassoc 帧", "Wireshark: wlan.fc.type_subtype==0x0c || wlan.fc.type_subtype==0x0a", "得到 reason code 分布"),
         ("区分发起方", "看帧源地址与 instigator", "终端主动 / AP 主动 / 可疑"),
         ("对齐 AP 侧事件", "AP 日志、DFS/信道切换、射频复位", "是否有共因"),
         ("按码分流处置", "认证 / 安全套件 / 信道质量 / PMF", "复测掉线次数")),
       ["cisco-dot11", "cisco-reasons"],
       "疑似伪造 deauth 或 PMF 兼容问题，附 OTA 开 case。",
       "恢复 PMF 与安全策略原配置。"),

    pb("PB-GEN-007", "随机 MAC / iPSK 策略失效", ["Cisco", "通用"], "认证", "medium",
       "「同一台手机有时能连、有时不能」「按 MAC 发的专用密码不生效」。",
       ["random MAC", "随机 MAC", "iPSK", "private address", "MAB", "EXCLUDE_POLICY_BIND_FAIL"],
       "iOS 14 / Android 10 起终端默认使用随机 MAC，会破坏基于 MAC 的 iPSK / MAB / 静态绑定策略；官方 9800 iPSK 文档明确提示需关闭该特性。",
       P("您的设备启用了「私有地址/随机 MAC」，会导致按设备识别的网络策略无法匹配；请在该 Wi-Fi 的设置里关闭随机地址后重试。",
         "核对终端是否启用随机 MAC；若策略依赖 MAC，改为按用户/证书授权，或要求终端对该 SSID 关闭随机地址；不要误判为信号问题。",
         "问题描述：SSID [名称] 启用 iPSK/MAB，终端 [型号/系统] 因随机 MAC 无法匹配策略。附件：认证日志、终端 MAC 变化记录、WLAN 策略配置。请确认推荐的兼容做法。"),
       S(("确认终端 MAC 是否变化", "控制器客户端表 / 认证日志", "同一设备出现多个 MAC"),
         ("核对策略依赖", "iPSK / MAB / 静态绑定配置", "确认依赖 MAC"),
         ("指导终端关闭随机地址", "该 SSID 的「私有地址」设为关闭", "MAC 稳定"),
         ("或改按用户/证书授权", "调整 AAA 策略", "不再依赖 MAC")),
       ["cisco-reasons", "cisco-213970"],
       "关闭随机 MAC 后仍失败，附认证日志开 case。",
       "恢复原 AAA 策略配置。"),

    pb("PB-GEN-008", "DHCP 地址池耗尽 / 广播风暴导致大面积「连上没网」", ["通用"], "网络层", "high",
       "「开会时人一多就连不上」「连上但没网」。",
       ["DHCP", "地址池", "pool", "169.254", "广播风暴", "Discover", "Offer"],
       "高密场景地址池容量或上联二层问题，属网络层；工具应把这类结论与射频类结论明确区分。",
       P("该区域同时接入的设备较多，地址分配资源暂时不足，我们正在扩容并核查上联链路，稍后重新连接即可。",
         "核对 DHCP 池余量与租约、VLAN 是否放行、上联端口广播/CRC 错包；官方 FortiAP 排错特别提示 AP 数量增长后 DHCP 池可能不够用。",
         "问题描述：[时间/区域] 多台终端关联成功但拿不到地址（169.254）。附件：DHCP 服务器日志与池使用率、上联端口统计、控制器客户端日志。请协助确认是否存在二层环路或池容量问题。"),
       S(("确认现象范围", "按 VLAN / AP / SSID 聚合失败终端", "判断是否共因"),
         ("核对 DHCP 池", "池使用率、租约时间与中继", "有可用地址"),
         ("核对 VLAN 与上联", "接口 VLAN、Trunk 放行、端口错包", "二层链路正常"),
         ("复测", "多台终端并发接入", "均能获取地址")),
       ["fgt-kb114487", "cisco-reasons"],
       "池充足且二层正常仍失败，附 DHCP debug 与抓包开 case。",
       "扩容/租约调整可回退；保留原池配置。"),

    pb("PB-GEN-009", "控制面报文阶段化分析（PCAP 导入）", ["通用"], "取证", "info",
       "「我抓了包，但看不懂卡在哪一步」。",
       ["pcap", "DISCOVERY_REQ", "DISCOVERY_RESP", "JOIN_REQ", "CFG_STATUS", "DATA_CHAN_KEEP_ALIVE", "ECHO_REQ", "CWAS_RUN"],
       "把抓包解析为阶段序列：AP 侧 Discovery → Response → DTLS → Join → Configuration → Data Channel → RUN，客户端侧 Association → 802.1X → DHCP → 数据面；缺哪段就定位到哪段。",
       P("我们已经拿到故障时段的网络记录，正在定位具体中断环节，不需要您重复操作。",
         "官方 FortiAP 成功关联示例给出完整状态机（CWAS_START/IDLE/DTLS_SETUP/DTLS_AUTHORIZE/DTLS_CONN/JOIN/CONFIG/DATA_CHAN_SETUP/DATA_CHECK/RUN），DTLS 响应慢或缺失通常指向配置问题。",
         "问题描述：AP [序列号] 的 CAPWAP 抓包在 [阶段] 后无后续报文。附件：PCAP（已脱敏）、控制器侧 cw_acd debug、AP 侧日志、双侧时间基准。请确认该阶段的失败原因。"),
       S(("导入 PCAP 或日志文本", "工具离线分析（不上传、不外发）", "识别出阶段序列"),
         ("标出首个缺失阶段", "与官方状态机对照", "定位到具体阶段"),
         ("按阶段取补充证据", "DTLS → 证书/时间；Discovery → 地址/管理访问；JOIN → 授权/版本", "证据闭环"),
         ("归档", "保存阶段结论与原文引用", "可回溯")),
       ["fgt-266575", "fgt-kb114487"],
       "阶段齐全但 AP 仍不上线，附双侧日志开 case。",
       "无需变更；抓包结束后停止 sniff。"),
]

# ============================================================ 5. 通用话术模板
PHRASE_TEMPLATES = [
    dict(scene="首次响应投诉", audience="业务用户",
         text="我们已收到您反馈的 [时间/地点] 无线问题，正在核查该区域的接入点与控制器记录。请先不要反复重启设备或忘记网络，核查完成后会同步结论。"),
    dict(scene="需要现场复现", audience="业务用户",
         text="为定位问题，需要在 [时间] 用您的设备复现一次连接过程，大约 [X] 分钟；期间我们会同步记录设备侧日志，不会影响其他业务。"),
    dict(scene="确认为配置/平台问题", audience="业务用户",
         text="已确认问题出在网络配置/控制平台，不是您的设备或信号强度问题。我们会按变更流程修复，完成后通知您重新连接一次即可。"),
    dict(scene="确认为覆盖/干扰问题", audience="业务用户",
         text="已确认该位置信号覆盖或干扰不达标，我们正在评估接入点位置与信道调整，会先做仿真再安排变更，期间建议暂时靠近 [位置] 使用。"),
    dict(scene="阶段性同步", audience="管理侧",
         text="当前定位到 [阶段]，证据为 [日志/原因码/抓包]，影响范围 [AP/SSID/终端数]；已排除 [已排除项]；下一步 [动作]，预计复测方式 [方法]。"),
    dict(scene="结案回复", audience="业务用户",
         text="问题已处理完毕：根因 [结论]，处置 [动作]，复测结果 [指标]。如再次出现请提供时间、地点与终端型号，我们会按同一证据链复查。"),
    dict(scene="厂商工单开场", audience="厂商支持",
         text="设备：[厂商/型号/软件版本]；现象：[症状]，首次出现 [时间]，影响 [范围]；已执行 [官方手册章节/命令]；已排除 [项目]；附件：[日志清单]。请协助确认 [具体问题]。"),
    dict(scene="升级判断", audience="内部",
         text="满足以下任一条件即升级：出现守护进程崩溃日志、多 AP 同时掉线、疑似已知缺陷/版本兼容问题、按官方步骤核查后仍无法解释、涉及业务中断且需厂商确认。"),
]

# ============================================================ 组装
def main():
    cisco = load_cisco_codes()
    ruckus = [dict(code=str(c), name=n, zh=z, action=a, vendor="RUCKUS SmartZone")
              for c, n, z, a in RUCKUS_CODES]
    dot11 = [dict(code=str(c), name=n, zh=z, action=a, vendor="IEEE 802.11 / Cisco")
             for c, n, z, a in DOT11_REASONS]

    # 合并扩展模块（文献 / 命令 / 剧本 / 话术 / SOP）
    docs, commands, playbooks, phrases, sops = wlc_kb_extra.merge_into(
        DOCS, COMMANDS, PLAYBOOKS, PHRASE_TEMPLATES)
    doc_vendors = Counter(x["vendor"] for x in docs)
    doc_topics = Counter(x.get("topic") or "未分类" for x in docs)
    doc_status = Counter(x.get("verify_status") or "public-verified" for x in docs)

    payload = dict(
        schema_version="2.0",
        generated_at=DATE,
        offline=True,
        offline_notice="本知识包全部内置于程序，运行时不访问任何外部网络；URL 仅作离线引用展示，不发起请求。",
        license_notice=("厂商公开文档的索引、事实性码表与中文摘要；不内置文档全文。"
                        "Cisco 原因码的 Explanation / Action 保留官方英文原文，中文提示为自动归纳，界面须同时展示原文。"
                        "社区资料与待核对条目已单独标注，不得直接用于自动决策。"),
        sources_note=("提炼自各厂商 2026-11-10 可公开访问的官方文档与知识库文章；"
                      "Ruckus 不同版本页面对 8443/wsg/api/public 与 7443/api/public 的 Recommend/Deprecated 标注存在渲染差异，接入前以设备实际版本 API Reference 为准。"),
        counts=dict(
            cisco_client_delete_reasons=len(cisco),
            ruckus_api_codes=len(ruckus),
            ieee80211_reason_codes=len(dot11),
            fortigate_wtp_failures=len(FGT_FAILURES),
            h3c_community_codes=len(H3C_CODES),
            playbooks=len(playbooks),
            playbook_steps=sum(len(p["steps"]) for p in playbooks),
            doc_entries=len(docs),
            doc_vendors=len(doc_vendors),
            doc_topics=len(doc_topics),
            doc_verified=doc_status.get("public-verified", 0),
            doc_needs_check=doc_status.get("needs-field-check", 0),
            doc_community=doc_status.get("community", 0),
            commands=len(commands),
            phrase_templates=len(phrases),
            sop_entries=len(sops),
        ),
        doc_facets=dict(vendors=dict(doc_vendors), topics=dict(doc_topics),
                        verify_status=dict(doc_status)),
        severity_legend={
            "critical": "业务中断 / 大面积影响，需立即处置",
            "high": "明确影响部分用户或存在配置错误，需尽快处置",
            "medium": "性能或稳定性问题，按计划处置",
            "low": "预期行为或误报抑制，记录即可",
            "info": "取证/方法学条目，无直接变更动作",
        },
        severity_cisco=Counter(x["severity_zh"] for x in cisco),
        error_codes=dict(
            cisco_9800_client_delete=dict(
                vendor="Cisco", platform="Catalyst 9800 / IOS XE 17.x",
                source_doc="cisco-reasons", count=len(cisco),
                note="官方 270 条全量内置；severity 与 explanation/action 为官方英文原文，category/hint_zh 为自动归纳",
                records=cisco),
            ruckus_sz_api=dict(vendor="RUCKUS", platform="SmartZone / vSZ",
                               source_doc="rkz-api6", count=len(ruckus), records=ruckus),
            ieee80211_reason=dict(vendor="通用 / IEEE 802.11", platform="空口帧",
                                  source_doc="cisco-dot11", count=len(dot11), records=dot11),
            fortigate_wtp_failure=dict(vendor="Fortinet", platform="FortiGate 内置 WLC",
                                       source_doc="fgt-kb114487", count=len(FGT_FAILURES),
                                       note="官方公开资料明确给出的失败码仅 8；其余数字不臆造，现场以设备输出为准",
                                       records=FGT_FAILURES),
            h3c_community=dict(vendor="H3C", platform="WX / Comware V7",
                               source_doc="h3c-kms", count=len(H3C_CODES),
                               note="社区资料，非官方手册条文，现场须复核", records=H3C_CODES),
        ),
        playbooks=playbooks,
        doc_library=docs,
        commands=commands,
        phrase_templates=phrases,
        sop_library=sops,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("output:", OUT)
    for k, v in payload["counts"].items():
        print(" ", k, v)
    print(" cisco severity:", dict(payload["severity_cisco"]))
    print(" size:", OUT.stat().st_size, "B")


if __name__ == "__main__":
    main()
