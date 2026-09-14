import socket
import threading
import time
import random
import argparse
import os
import ipaddress
from typing import Dict, Tuple, Optional, Set


# ============================================================
# DNS Relay 配置
# ============================================================

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 53

# 上游 DNS
UPSTREAM_DNS = ("114.114.114.114", 53)

# DNS 配置文件
CONFIG_FILE = "dnsrelay.txt"

# 上游 DNS 超时时间
FORWARD_TIMEOUT = 5.0

# UDP 接收缓冲区
BUF_SIZE = 4096

# 转发映射最大保存时间
MAP_TIMEOUT = 6.0


# ============================================================
# 全局数据
# ============================================================

# 域名 -> {DNS 类型: IP}，1=A，28=AAAA
domain_table: Dict[str, Dict[int, str]] = {}

# 配置为 0.0.0.0 的域名对所有查询类型返回 NXDOMAIN
blocked_domains: Set[str] = set()

# new_dns_id ->
# (old_dns_id, client_ip, client_port, expire_time)
forward_map: Dict[
    int,
    Tuple[int, str, int, float]
] = {}

map_lock = threading.Lock()

# 0：普通运行；1：-d 调试摘要；2：-dd 报文字节
DEBUG_LEVEL = 0


# ============================================================
# 日志
# ============================================================

def log(message: str):
    """统一输出日志"""
    print(message, flush=True)


def debug_log(message: str, level: int = 1):
    """按 -d / -dd 级别输出调试信息。"""
    if DEBUG_LEVEL >= level:
        prefix = "DEBUG" if level == 1 else "DEBUG2"
        log(f"[{prefix}] {message}")


# ============================================================
# 加载 dnsrelay.txt
# ============================================================

def load_config(filename: str):
    """
    加载 DNS 本地配置。

    文件格式：

        0.0.0.0 www.666.com
        114.255.40.66 www.bupt.com.cn
        2001:db8::66 www.bupt.com.cn

    第一列：IP
    第二列：域名
    """

    global domain_table, blocked_domains

    domain_table.clear()
    blocked_domains.clear()

    if not os.path.exists(filename):
        log(f"[CONFIG] 找不到配置文件: {filename}")
        return

    try:
        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            for line_number, line in enumerate(f, 1):

                line = line.strip()

                # 空行
                if not line:
                    continue

                # 注释
                if line.startswith("#"):
                    continue

                parts = line.split()

                if len(parts) < 2:
                    log(
                        f"[CONFIG] 第 {line_number} 行格式错误，已跳过"
                    )
                    continue

                ip = parts[0].strip()
                domain = parts[1].strip().lower().rstrip(".")

                # 0.0.0.0 是项目约定的整域名屏蔽标记
                if ip == "0.0.0.0":
                    blocked_domains.add(domain)
                    domain_table.pop(domain, None)
                    continue

                # 同时接受 IPv4 和 IPv6
                try:
                    address = ipaddress.ip_address(ip)
                except ValueError:
                    log(
                        f"[CONFIG] 第 {line_number} 行 IP 无效: {ip}"
                    )
                    continue

                if domain in blocked_domains:
                    log(
                        f"[CONFIG] 第 {line_number} 行已跳过: "
                        f"{domain} 已被 0.0.0.0 屏蔽"
                    )
                    continue

                qtype = 1 if address.version == 4 else 28
                domain_table.setdefault(domain, {})[qtype] = str(address)

        record_count = sum(
            len(records) for records in domain_table.values()
        ) + len(blocked_domains)

        log(
            f"[CONFIG] 加载完成，共 {record_count} 条记录"
        )

        for domain in sorted(blocked_domains):
            log(f"          {domain} -> 0.0.0.0 [BLOCK]")

        for domain, records in domain_table.items():
            for qtype, ip in records.items():
                record_type = "A" if qtype == 1 else "AAAA"
                log(f"          {domain} -> {ip} [{record_type}]")

    except Exception as e:
        log(f"[CONFIG] 读取配置失败: {e}")


# ============================================================
# DNS 域名解析
# ============================================================

def parse_domain(
    data: bytes,
    start: int = 12
) -> Tuple[Optional[str], int]:
    """
    从 DNS 报文中解析域名。

    支持：
    1. 普通 DNS Label
    2. DNS 压缩指针

    返回：
        (域名, 原始域名字段结束位置)

    例如：
        www.baidu.com
    """

    try:

        labels = []

        pos = start

        # 防止恶意/错误报文造成死循环
        visited = set()

        jumped = False
        end_pos = None

        while True:

            if pos >= len(data):
                return None, start

            if pos in visited:
                return None, start

            visited.add(pos)

            length = data[pos]

            # 域名结束
            if length == 0:

                if not jumped:
                    end_pos = pos + 1

                break

            # DNS 压缩指针
            if (length & 0xC0) == 0xC0:

                if pos + 1 >= len(data):
                    return None, start

                offset = (
                    ((length & 0x3F) << 8)
                    | data[pos + 1]
                )

                if offset >= len(data):
                    return None, start

                if not jumped:
                    end_pos = pos + 2

                pos = offset
                jumped = True

                continue

            # Label 长度不能超过 63
            if length > 63:
                return None, start

            pos += 1

            if pos + length > len(data):
                return None, start

            label = data[
                pos:pos + length
            ]

            try:
                label_text = label.decode(
                    "ascii"
                )
            except UnicodeDecodeError:
                return None, start

            labels.append(label_text)

            pos += length

        domain = ".".join(labels).lower().rstrip(".")

        if not domain:
            return None, start

        return domain, end_pos

    except Exception:
        return None, start


# ============================================================
# 解析 DNS Question
# ============================================================

def parse_question(
    data: bytes
):
    """
    解析 DNS Question。

    返回：
        domain
        qtype
        qclass
        question_end
    """

    # DNS Header 最少 12 字节
    if len(data) < 12:
        return None

    domain, pos = parse_domain(data, 12)

    if domain is None:
        return None

    # QTYPE + QCLASS
    if pos + 4 > len(data):
        return None

    qtype = int.from_bytes(
        data[pos:pos + 2],
        "big"
    )

    qclass = int.from_bytes(
        data[pos + 2:pos + 4],
        "big"
    )

    question_end = pos + 4

    return (
        domain,
        qtype,
        qclass,
        question_end
    )


# ============================================================
# 构造 DNS 错误响应
# ============================================================

def build_error_response(
    request: bytes,
    rcode: int
) -> bytes:
    """
    构造 DNS 错误响应。

    rcode:
        1 = FORMERR
        2 = SERVFAIL
    """

    if len(request) < 2:
        return b""

    transaction_id = request[:2]

    # 尽量保留 RD
    request_flags = 0

    if len(request) >= 4:
        request_flags = int.from_bytes(
            request[2:4],
            "big"
        )

    # QR = 1
    # RD = 保留请求中的 RD
    # RA = 1
    # RCODE = rcode
    flags = (
        0x8000
        | (request_flags & 0x0100)
        | 0x0080
        | rcode
    )

    qdcount = b"\x00\x01"

    if len(request) < 12:
        qdcount = b"\x00\x00"

    header = (
        transaction_id
        + flags.to_bytes(2, "big")
        + qdcount
        + b"\x00\x00"   # ANCOUNT
        + b"\x00\x00"   # NSCOUNT
        + b"\x00\x00"   # ARCOUNT
    )

    # 如果请求格式完整，保留 Question
    if len(request) >= 12:

        question = request[12:]

        return header + question

    return header


# ============================================================
# 构造本地 A 记录响应
# ============================================================

def build_local_a_response(
    request: bytes,
    answer_ip: str
) -> bytes:
    """
    为本地配置的 IPv4 地址构造 DNS A 响应。
    """

    return build_local_ip_response(request, answer_ip, 1)


def build_local_aaaa_response(
    request: bytes,
    answer_ip: str
) -> bytes:
    """为本地配置的 IPv6 地址构造 DNS AAAA 响应。"""
    return build_local_ip_response(request, answer_ip, 28)


def build_local_ip_response(
    request: bytes,
    answer_ip: str,
    expected_qtype: int
) -> bytes:
    """构造本地 A 或 AAAA 响应。"""

    parsed = parse_question(request)

    if parsed is None:
        return build_error_response(
            request,
            1
        )

    domain, qtype, qclass, question_end = parsed

    if qtype != expected_qtype or qclass != 1:
        return build_error_response(
            request,
            2
        )

    try:
        family = socket.AF_INET if qtype == 1 else socket.AF_INET6
        ip_bytes = socket.inet_pton(family, answer_ip)
    except OSError:
        return build_error_response(
            request,
            2
        )

    transaction_id = request[:2]

    request_flags = int.from_bytes(
        request[2:4],
        "big"
    )

    # QR = 1
    # AA = 1
    # RD 保留
    # RA = 1
    flags = (
        0x8000
        | 0x0400
        | (request_flags & 0x0100)
        | 0x0080
    )

    header = (
        transaction_id
        + flags.to_bytes(2, "big")
        + b"\x00\x01"  # QDCOUNT
        + b"\x00\x01"  # ANCOUNT
        + b"\x00\x00"  # NSCOUNT
        + b"\x00\x00"  # ARCOUNT
    )

    # 原始 Question
    question = request[12:question_end]

    # DNS Name Pointer -> Question 中的域名
    name_pointer = b"\xC0\x0C"

    # TYPE A 或 AAAA
    record_type = qtype.to_bytes(2, "big")

    # CLASS IN
    class_in = b"\x00\x01"

    # TTL = 60 秒
    ttl = b"\x00\x00\x00\x3C"

    # IPv4 为 4 字节，IPv6 为 16 字节
    rdlength = len(ip_bytes).to_bytes(2, "big")

    answer = (
        name_pointer
        + record_type
        + class_in
        + ttl
        + rdlength
        + ip_bytes
    )

    return (
        header
        + question
        + answer
    )


# ============================================================
# 构造本地 NXDOMAIN 响应
# ============================================================

def build_nxdomain_response(
    request: bytes
) -> bytes:
    """
    为配置为 0.0.0.0 的域名构造 NXDOMAIN 响应。

    NXDOMAIN 的 RCODE 为 3，且不包含 Answer 记录。
    """

    parsed = parse_question(request)

    if parsed is None:
        return build_error_response(
            request,
            1
        )

    _, _, _, question_end = parsed

    transaction_id = request[:2]

    request_flags = int.from_bytes(
        request[2:4],
        "big"
    )

    # QR = 1
    # AA = 1
    # RD = 保留请求中的 RD
    # RA = 1
    # RCODE = 3 (NXDOMAIN)
    flags = (
        0x8000
        | 0x0400
        | (request_flags & 0x0100)
        | 0x0080
        | 0x0003
    )

    header = (
        transaction_id
        + flags.to_bytes(2, "big")
        + b"\x00\x01"  # QDCOUNT
        + b"\x00\x00"  # ANCOUNT
        + b"\x00\x00"  # NSCOUNT
        + b"\x00\x00"  # ARCOUNT
    )

    # 保留客户端的原始 Question
    question = request[12:question_end]

    return header + question


# ============================================================
# 生成不重复的 DNS ID
# ============================================================

def generate_dns_id() -> int:

    for _ in range(100):

        new_id = random.randint(
            0,
            65535
        )

        with map_lock:

            if new_id not in forward_map:
                return new_id

    # 极端情况下顺序查找
    with map_lock:

        for new_id in range(65536):

            if new_id not in forward_map:
                return new_id

    raise RuntimeError(
        "DNS ID 已耗尽"
    )


# ============================================================
# 清理超时映射
# ============================================================

def timeout_cleaner():

    while True:

        now = time.time()

        expired = []

        with map_lock:

            for dns_id, value in forward_map.items():

                expire_time = value[3]

                if now > expire_time:
                    expired.append(dns_id)

            for dns_id in expired:

                del forward_map[dns_id]

        if expired:

            log(
                f"[TIMEOUT] 清理 {len(expired)} 个超时请求"
            )

        time.sleep(1)


# ============================================================
# 接收上游 DNS 响应
# ============================================================

def upstream_receiver(
    upstream_sock: socket.socket,
    listen_sock: socket.socket
):

    while True:

        try:

            response, _ = upstream_sock.recvfrom(
                BUF_SIZE
            )

            # DNS Header 至少12字节
            if len(response) < 12:
                log("[RESPONSE] 收到非法 DNS 响应")
                continue

            new_id = int.from_bytes(
                response[:2],
                "big"
            )

            # 查找转发记录
            with map_lock:

                mapping = forward_map.pop(
                    new_id,
                    None
                )

            if mapping is None:

                log(
                    f"[RESPONSE] 未找到 DNS ID: {new_id}"
                )

                continue

            (
                old_id,
                client_ip,
                client_port,
                _
            ) = mapping

            # 恢复客户端原来的 Transaction ID
            response = (
                old_id.to_bytes(
                    2,
                    "big"
                )
                + response[2:]
            )

            # 返回客户端
            listen_sock.sendto(
                response,
                (
                    client_ip,
                    client_port
                )
            )

            log(
                f"[RESPONSE] "
                f"ID {new_id} -> {old_id} "
                f"返回 {client_ip}:{client_port}"
            )

        except OSError as e:

            # 主线程退出并关闭 socket 后，接收线程应安静结束，
            # 避免在 Ctrl+C 时反复打印 Bad file descriptor。
            if upstream_sock.fileno() == -1 or getattr(e, "errno", None) in (9, 10038):
                break

            log(
                f"[RESPONSE] Socket错误: {e}"
            )

        except Exception as e:

            log(
                f"[RESPONSE] 异常: {e}"
            )


# ============================================================
# 参数
# ============================================================

def parse_arguments():

    parser = argparse.ArgumentParser(
        description="Python DNS Relay",
        usage=(
            "%(prog)s [-d|-dd] [dns-server-ipaddr] [filename] "
            "[--host HOST] [--port PORT] [--timeout SECONDS]"
        )
    )

    parser.add_argument(
        "-d",
        dest="debug_level",
        action="count",
        default=0,
        help="输出调试摘要；-dd 额外输出 DNS 报文字节"
    )

    parser.add_argument(
        "dns_server_ipaddr",
        nargs="?",
        help="上游 DNS IPv4 地址（兼容课程 PPT 的位置参数）"
    )

    parser.add_argument(
        "filename",
        nargs="?",
        help="本地域名配置文件（兼容课程 PPT 的位置参数）"
    )

    parser.add_argument(
        "--host",
        default=LISTEN_HOST,
        help="监听地址，默认 0.0.0.0"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=LISTEN_PORT,
        help="监听端口，默认 53"
    )

    parser.add_argument(
        "--upstream",
        dest="upstream_option",
        help="上游 DNS；与位置参数 dns-server-ipaddr 二选一"
    )

    parser.add_argument(
        "--config",
        dest="config_option",
        help="配置文件；与位置参数 filename 二选一"
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=FORWARD_TIMEOUT,
        help="DNS 转发超时时间，默认 5 秒"
    )

    args = parser.parse_args()

    if args.upstream_option and args.dns_server_ipaddr:
        parser.error("不能同时使用位置参数 dns-server-ipaddr 和 --upstream")

    if args.config_option and args.filename:
        parser.error("不能同时使用位置参数 filename 和 --config")

    args.upstream = (
        args.upstream_option
        or args.dns_server_ipaddr
        or UPSTREAM_DNS[0]
    )
    args.config = (
        args.config_option
        or args.filename
        or CONFIG_FILE
    )

    try:
        socket.inet_aton(args.upstream)
    except OSError:
        parser.error(f"无效的上游 DNS IPv4 地址: {args.upstream}")

    args.debug_level = min(args.debug_level, 2)
    return args


# ============================================================
# 主程序
# ============================================================

def main():

    global DEBUG_LEVEL

    args = parse_arguments()
    DEBUG_LEVEL = args.debug_level

    upstream_dns = (
        args.upstream,
        53
    )

    # --------------------------------------------------------
    # 加载配置
    # --------------------------------------------------------

    load_config(
        args.config
    )

    # --------------------------------------------------------
    # 创建客户端监听 Socket
    # --------------------------------------------------------

    listen_sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    listen_sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1
    )

    try:

        listen_sock.bind(
            (
                args.host,
                args.port
            )
        )

    except OSError as e:

        log(
            f"[ERROR] 无法监听 "
            f"{args.host}:{args.port}"
        )

        log(
            f"[ERROR] {e}"
        )

        log(
            "[提示] Windows 下监听 53 端口通常需要管理员权限，"
            "或者检查端口是否已经被占用。"
        )

        return

    # --------------------------------------------------------
    # 创建上游 DNS Socket
    # --------------------------------------------------------

    upstream_sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    # Windows 下使用独立 UDP Socket
    upstream_sock.bind(
        (
            "0.0.0.0",
            0
        )
    )

    # --------------------------------------------------------
    # 启动信息
    # --------------------------------------------------------

    log("")
    log("=" * 60)
    log("                 DNS Relay")
    log("=" * 60)
    log(f"[START] 监听地址 : {args.host}:{args.port}")
    log(
        f"[START] 上游 DNS : "
        f"{upstream_dns[0]}:{upstream_dns[1]}"
    )
    log(
        f"[START] 配置文件 : {args.config}"
    )
    log(
        f"[START] 超时时间 : {args.timeout}s"
    )
    local_record_count = sum(
        len(records) for records in domain_table.values()
    ) + len(blocked_domains)
    log(f"[START] 本地记录 : {local_record_count} 条")
    log(
        f"[START] 调试级别 : "
        f"{DEBUG_LEVEL}"
    )
    log("=" * 60)
    log("")

    # --------------------------------------------------------
    # 后台线程
    # --------------------------------------------------------

    threading.Thread(
        target=timeout_cleaner,
        daemon=True
    ).start()

    threading.Thread(
        target=upstream_receiver,
        args=(
            upstream_sock,
            listen_sock
        ),
        daemon=True
    ).start()

    # --------------------------------------------------------
    # 主循环：接收客户端 DNS 请求
    # --------------------------------------------------------

    while True:

        try:

            request, client_addr = (
                listen_sock.recvfrom(
                    BUF_SIZE
                )
            )

            client_ip, client_port = client_addr

            debug_log(
                f"收到客户端 UDP 报文，来源={client_ip}:{client_port}，"
                f"长度={len(request)} 字节"
            )
            debug_log(
                f"客户端报文 HEX={request.hex(' ')}",
                level=2
            )

            # ------------------------------------------------
            # 基本报文检查
            # ------------------------------------------------

            if len(request) < 12:

                log(
                    f"[FORMERR] "
                    f"{client_ip}:{client_port} "
                    f"报文长度不足"
                )

                response = build_error_response(
                    request,
                    1
                )

                if response:
                    listen_sock.sendto(
                        response,
                        client_addr
                    )

                continue

            # ------------------------------------------------
            # 检查 QR
            # ------------------------------------------------

            flags = int.from_bytes(
                request[2:4],
                "big"
            )

            qr = (
                flags >> 15
            ) & 1

            # 我们只接受查询报文
            if qr != 0:

                log(
                    "[FORMERR] 收到的不是 DNS Query"
                )

                response = build_error_response(
                    request,
                    1
                )

                listen_sock.sendto(
                    response,
                    client_addr
                )

                continue

            # ------------------------------------------------
            # 解析 Question
            # ------------------------------------------------

            parsed = parse_question(
                request
            )

            if parsed is None:

                log(
                    f"[FORMERR] "
                    f"{client_ip}:{client_port} "
                    f"DNS Question 格式错误"
                )

                response = build_error_response(
                    request,
                    1
                )

                listen_sock.sendto(
                    response,
                    client_addr
                )

                continue

            domain, qtype, qclass, _ = parsed

            qtype_name = {
                1: "A",
                2: "NS",
                5: "CNAME",
                6: "SOA",
                12: "PTR",
                15: "MX",
                16: "TXT",
                28: "AAAA"
            }.get(
                qtype,
                str(qtype)
            )

            log(
                f"[QUERY] "
                f"{domain} "
                f"TYPE={qtype_name} "
                f"CLASS={qclass} "
                f"FROM={client_ip}:{client_port}"
            )

            # ------------------------------------------------
            # 本地配置查询
            # ------------------------------------------------

            if qclass == 1 and domain in blocked_domains:

                log(
                    f"[NXDOMAIN] "
                    f"{domain} TYPE={qtype_name} -> 域名不存在"
                )

                response = build_nxdomain_response(request)
                listen_sock.sendto(response, client_addr)
                continue

            local_records = domain_table.get(domain, {})

            if qclass == 1 and qtype in local_records:

                answer_ip = local_records[qtype]

                log(
                    f"[LOCAL] {domain} "
                    f"TYPE={qtype_name} -> {answer_ip}"
                )

                if qtype == 1:
                    response = build_local_a_response(request, answer_ip)
                else:
                    response = build_local_aaaa_response(request, answer_ip)

                listen_sock.sendto(response, client_addr)
                continue

            # ------------------------------------------------
            # 非 A / 非 IN 请求：
            # 直接转发给上游
            # ------------------------------------------------

            # ------------------------------------------------
            # 生成新的 DNS ID
            # ------------------------------------------------

            old_dns_id = int.from_bytes(
                request[:2],
                "big"
            )

            new_dns_id = generate_dns_id()

            expire_time = (
                time.time()
                + args.timeout
            )

            # ------------------------------------------------
            # 保存映射
            # ------------------------------------------------

            with map_lock:

                forward_map[new_dns_id] = (
                    old_dns_id,
                    client_ip,
                    client_port,
                    expire_time
                )

            # ------------------------------------------------
            # 修改 DNS Transaction ID
            # ------------------------------------------------

            forward_packet = (
                new_dns_id.to_bytes(
                    2,
                    "big"
                )
                + request[2:]
            )

            # ------------------------------------------------
            # 转发
            # ------------------------------------------------

            try:

                upstream_sock.sendto(
                    forward_packet,
                    upstream_dns
                )

            except Exception:

                with map_lock:
                    forward_map.pop(
                        new_dns_id,
                        None
                    )

                response = build_error_response(
                    request,
                    2
                )

                listen_sock.sendto(
                    response,
                    client_addr
                )

                log(
                    f"[SERVFAIL] "
                    f"无法发送到上游 DNS: "
                    f"{upstream_dns}"
                )

                continue

            log(
                f"[FORWARD] "
                f"{domain} "
                f"TYPE={qtype_name} "
                f"ID={old_dns_id}->{new_dns_id} "
                f"UPSTREAM={upstream_dns[0]}"
            )

        except KeyboardInterrupt:

            log("")
            log("[STOP] DNS Relay 已停止")
            break

        except Exception as e:

            log(
                f"[ERROR] 主循环异常: {e}"
            )

    # --------------------------------------------------------
    # 关闭 Socket
    # --------------------------------------------------------

    listen_sock.close()
    upstream_sock.close()


# ============================================================
# 程序入口
# ============================================================

if __name__ == "__main__":
    main()
