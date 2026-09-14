# DNS Relay 的 Wireshark 抓包与测试结果

本文用于完成课程 PPT 第 23、26、27、28、35 页对应的 Wireshark 分析和 Testing and results。Wireshark 只用于观察 DNS Relay 实际发送、转发和接收的报文，不属于中继器代码的一部分。

## 1. 抓包环境

- 操作系统：Windows 10/11。
- DNS Relay：`dns_relay.py`，监听 `127.0.0.1:53`。
- 客户端：Windows `nslookup`。
- 抓包工具：Wireshark，安装时同时安装 Npcap，并启用 loopback 抓包支持。
- 上游 DNS：默认 `114.114.114.114:53`。

如果 Windows 上的 UDP 53 已被系统服务占用，应先停止占用者。`nslookup` 不能指定非标准 DNS 端口，因此用课程要求的 `nslookup` 演示时必须让 Relay 监听 53，而不是 10053。管理员身份主要用于避免 Wireshark/Npcap 捕获权限问题；能否绑定 53 还取决于该端口是否空闲。

## 2. 开始抓包

1. 以管理员身份启动 Wireshark。
2. 同时选择 `Npcap Loopback Adapter` 和当前实际联网的 Ethernet/Wi-Fi 网卡。只抓 loopback 会漏掉 Relay 与上游 DNS 的通信，只抓物理网卡会漏掉客户端与 Relay 的通信。
3. 在捕获过滤器中输入：

   ```text
   udp port 53
   ```

4. 点击开始抓包。
5. 以管理员身份打开 PowerShell，进入项目目录后执行：

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\run_wireshark_demo.ps1
   ```

6. 三组查询完成后停止抓包，并保存为 `dns-relay-demo.pcapng`。

如果上游 DNS 在当前网络不可达，可改用当前网络允许访问的 DNS：

```powershell
.\run_wireshark_demo.ps1 -Upstream 8.8.8.8
```

## 3. Wireshark 显示过滤器

捕获完成后，在顶部显示过滤器输入以下表达式：

| 目的 | 显示过滤器 |
| --- | --- |
| 全部 DNS 报文 | `dns` |
| 全部 UDP DNS 报文 | `udp.port == 53` |
| 只看查询 | `dns.flags.response == 0` |
| 只看响应 | `dns.flags.response == 1` |
| 某个域名 | `dns.qry.name == "www.bupt.com.cn"` |
| 成功响应 | `dns.flags.response == 1 && dns.flags.rcode == 0` |
| NXDOMAIN | `dns.flags.response == 1 && dns.flags.rcode == 3` |
| 本地 A 记录结果 | `dns.a == 114.255.40.66` |
| 本地 AAAA 记录结果 | `dns.aaaa == 2001:db8::66` |
| 指定 Transaction ID | `dns.id == 0x1234`，其中 `0x1234` 换成抓包中的实际值 |

显示过滤器 `dns` 适合分析已经抓到的包。捕获过滤器 `udp port 53` 用于开始抓包前限制采集范围，两者不要填错位置。

## 4. DNS Request 截图要求

选中一条 `Standard query`，展开 `Domain Name System (query)`，截图中至少保留：

- Packet List 的 No.、Source、Destination、Protocol、Info。
- Transaction ID。
- Flags，确认 `Response: Message is a query`。
- Questions 数量。
- Queries 中的 Name、Type 和 Class。

报告中的说明可写：

> 客户端向 `127.0.0.1:53` 发送 UDP DNS 查询。DNS Header 的 QR 位为 0，表示 Request；Question 中记录了查询域名，A 类型表示查询 IPv4 地址，IN 表示 Internet 类。

## 5. DNS Response 截图要求

选中与 Request 对应的 `Standard query response`，展开 `Domain Name System (response)`，截图中至少保留：

- Transaction ID，并与对应 Request 对照。
- Flags，确认 `Response: Message is a response`。
- Reply code。
- Questions 和 Answer RRs 数量。
- Answers 中的 Name、Type、TTL 和 Address；NXDOMAIN 测试则展示 `Reply code: No such name (3)`。

报告中的说明可写：

> Relay 返回的 Response 使用与客户端 Request 相同的 Transaction ID，因此客户端能够把响应匹配到原查询。成功的本地 A 记录响应在 Answers 中包含 IPv4 地址；黑名单响应的 RCODE 为 3，且 Answer RRs 为 0。

## 6. Testing and results

### 测试 1：本地直接解析

命令：

```powershell
nslookup www.bupt.com.cn 127.0.0.1
```

预期结果：

- 客户端发出一个 A 查询，Relay 返回 A 响应 `114.255.40.66`。
- Request 与 Response 的 Transaction ID 相同。
- Relay 日志出现 `[LOCAL]`。
- 抓包中没有该域名发往上游 DNS 的查询，证明结果来自本地表。

建议截图过滤器：

```text
dns.qry.name == "www.bupt.com.cn"
```

### 测试 2：黑名单拦截

命令：

```powershell
nslookup www.666.com 127.0.0.1
```

预期结果：

- A 和 AAAA 查询均由 Relay 返回 NXDOMAIN，RCODE 为 3。
- Answer RRs 为 0。
- Request 与 Response 的 Transaction ID 相同。
- Relay 日志出现 `[NXDOMAIN]`。
- 抓包中没有该域名发往上游 DNS 的查询，证明黑名单在本地生效。

建议截图过滤器：

```text
dns.qry.name == "www.666.com"
```

### 测试 3：上游转发

命令：

```powershell
nslookup www.baidu.com 127.0.0.1
```

预期观察到四个方向的报文：

| 顺序 | 通信方向 | Transaction ID |
| --- | --- | --- |
| 1 | 客户端发给 Relay | 客户端原 ID |
| 2 | Relay 发给上游 DNS | Relay 分配的新 ID |
| 3 | 上游 DNS 发给 Relay | 与第 2 个报文相同 |
| 4 | Relay 发给客户端 | 恢复为客户端原 ID，与第 1 个报文相同 |

Relay 日志应依次出现 `[QUERY]`、`[FORWARD]` 和 `[RESPONSE]`。第 1、4 个包的 ID 相同，第 2、3 个包的 ID 相同，两组 ID 通常不同。这直接证明 Relay 实现了 Transaction ID 改写与恢复。

建议截图过滤器：

```text
dns.qry.name == "www.baidu.com"
```

## 7. 报告结果表

完成实际抓包后，把“实际结果”和包号填入下表。不要在未抓包时把预期值写成实测值。

| 编号 | 场景 | 预期结果 | 实际结果 | Request/Response 包号 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | 本地解析 | A=`114.255.40.66`，不访问上游 | 待填写 | 待填写 | 待填写 |
| 2 | 黑名单 | NXDOMAIN，Answer=0，不访问上游 | 待填写 | 待填写 | 待填写 |
| 3 | 本地 IPv6 | AAAA=`2001:db8::66`，不访问上游 | 待填写 | 待填写 | 待填写 |
| 4 | 上游转发 | 出现四向报文并完成 ID 改写/恢复 | 待填写 | 待填写 | 待填写 |

建议在报告中放四张核心截图：DNS Request 字段、DNS Response 字段、黑名单 NXDOMAIN、上游转发四报文及 Transaction ID 对照。每张截图下方写出包号、显示过滤器和观察结论。

## 8. 常见问题

- 只看到客户端与 Relay 两个包：检查是否同时选择了实际联网网卡。
- 只看到 Relay 与上游两个包：检查是否选择了 Npcap Loopback Adapter。
- 看不到包：确认 Wireshark 在执行 `nslookup` 前已经开始抓包，且捕获过滤器是 `udp port 53`。
- `nslookup` 超时：检查 Relay 是否成功绑定 53、Windows 防火墙是否拦截 Python、上游 DNS 是否可达。
- 出现额外 AAAA 查询：某些客户端会同时查询 IPv4 和 IPv6；分析时用 `dns.qry.type == 1` 只保留 A 查询。
- 包号很多：用 `dns.qry.name == "目标域名"` 缩小范围，再用 Transaction ID 配对请求与响应。
