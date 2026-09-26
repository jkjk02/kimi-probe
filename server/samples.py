"""Built-in text samples shared by tokenizer / hidden-prompt / cache probes.

The same texts are used by tools/build_baseline.py so that stored official
token counts line up with what the probes send.
"""

from __future__ import annotations

SYSTEM_NONE: list[dict] = []

# Samples for hidden prompt detection (increasing length, plain user turn only).
HIDDEN_PROMPT_SAMPLES: list[tuple[str, str]] = [
    ("hp_tiny", "hi"),
    (
        "hp_short",
        "请用三句话介绍一下上下文缓存（Context Caching）技术的原理，以及它在大语言模型服务中的典型应用场景。"
        "另外说明一下缓存命中率通常受哪些因素影响。",
    ),
    (
        "hp_medium",
        "下面是一段关于分布式系统的说明，请阅读后总结出五个要点。\n\n"
        "分布式系统由多台通过网络互联的计算机组成，它们协同工作以完成共同的任务。与单机系统相比，分布式系统在可扩展性、"
        "可用性和容错性方面具有显著优势，但同时也引入了新的复杂性：网络分区、时钟漂移、消息丢失与重复、部分失败等问题"
        "都需要在设计阶段被认真对待。CAP 定理指出，在网络分区不可避免的前提下，系统只能在一致性与可用性之间做出取舍。"
        "为了在实践中获得可接受的折中，工程师们发展出了多种一致性模型：线性一致性、顺序一致性、因果一致性以及最终一致性。"
        "共识算法（例如 Paxos 与 Raft）用于让一组节点就某个值达成一致，是构建强一致存储系统的基石。Raft 通过领导者选举、"
        "日志复制和安全性约束三个子问题把共识拆解得更易理解，因而被 etcd、TiKV 等系统广泛采用。另一方面，Dynamo 风格的系统"
        "选择了可用性优先，通过向量时钟、读修复和反熵协议在事后收敛副本状态。无论采取哪种路线，可观测性都是运维分布式系统"
        "的前提：结构化日志、指标与分布式追踪三者缺一不可。最后，混沌工程通过主动注入故障来验证系统的韧性，已经成为大型"
        "互联网公司保障服务稳定性的标准实践之一。",
    ),
    (
        "hp_long",
        "以下是一份关于 HTTP/3 与 QUIC 协议的技术备忘，请通读后回答：QUIC 相比 TCP 在连接建立与队头阻塞方面各有什么改进？\n\n"
        "HTTP/3 是超文本传输协议的第三个主要版本，其最大的变化在于底层传输不再使用 TCP，而是采用基于 UDP 的 QUIC 协议。"
        "QUIC 最初由 Google 设计，后经 IETF 标准化为 RFC 9000 系列。它把传输层、拥塞控制和加密（TLS 1.3）整合到同一层中，"
        "使得首次连接只需一个往返（1-RTT）即可完成握手，对于曾经连接过的服务器甚至可以做到 0-RTT 数据发送。相比之下，"
        "TCP 加 TLS 1.2 通常需要三个往返才能开始传输应用数据。\n\n"
        "队头阻塞（Head-of-Line Blocking）是 HTTP/2 在 TCP 之上多路复用时遗留的问题：由于 TCP 保证字节流的严格有序交付，"
        "任何一个丢包都会阻塞其后所有流的数据，即便这些流在逻辑上彼此独立。QUIC 在协议层原生支持多个独立的流，每个流"
        "拥有独立的流量控制与丢包恢复，因此单个流的丢包不再影响其他流。此外，QUIC 使用连接 ID 而非四元组来标识连接，"
        "这让客户端在切换网络（例如从 Wi-Fi 切换到蜂窝网络）时可以无缝迁移连接而无需重新握手。\n\n"
        "在拥塞控制方面，QUIC 把算法实现在用户态，这意味着可以在不升级操作系统内核的前提下快速迭代；它的 ACK 机制携带更"
        "精确的延迟信息，并且不存在 TCP 中重传歧义的问题，因为每个数据包都有唯一递增的包号。加密方面，除了极少数头部字段"
        "以外，QUIC 的绝大部分内容都是加密的，这减少了中间设备对协议的僵化（ossification）干扰。\n\n"
        "HTTP/3 在应用层沿用了 HTTP/2 的语义：请求与响应仍然通过流来承载，头部压缩改用了针对乱序交付设计的 QPACK 而非 HPACK。"
        "服务器推送、优先级等特性在 HTTP/3 中也有相应的调整。部署方面，由于许多企业网络会限制 UDP 流量，客户端通常会先尝试"
        "HTTP/3，失败后回退到 HTTP/2，服务器则通过 Alt-Svc 头部或 DNS HTTPS 记录向客户端宣告自己支持 HTTP/3。\n\n"
        "总的来说，QUIC 通过更快的握手、独立流的多路复用、连接迁移和用户态可演进的拥塞控制，解决了 TCP 在移动与高丢包"
        "环境下的多项痛点，但也带来了更高的 CPU 开销以及对 UDP 可达性的依赖。",
    ),
]

# Samples for tokenizer consistency (mixed language / json / code).
TOKENIZER_SAMPLES: list[tuple[str, str]] = [
    (
        "tk_zh",
        "人工智能正在深刻改变软件工程的实践方式。从代码补全到自动化测试生成，从需求分析到缺陷定位，大语言模型已经渗透到"
        "开发生命周期的每一个环节。然而，工具的进步并没有降低对工程师判断力的要求：模型可能产生看似合理却完全错误的输出，"
        "可能引入安全漏洞，也可能在边界条件上犯下低级错误。因此，成熟的团队会把模型输出视为需要评审的草稿，而不是可以直接"
        "合并的最终成果。与此同时，评估体系也在演进，从静态基准测试转向更贴近真实任务的端到端评测。",
    ),
    (
        "tk_mixed",
        "Deploy checklist for the `billing-service` (v2.3.1):\n"
        "1. 确认 Kubernetes 命名空间 `prod-billing` 的资源配额充足（CPU ≥ 8 cores, memory ≥ 32Gi）。\n"
        "2. Run database migration: `alembic upgrade head` — 预计耗时 3-5 分钟。\n"
        "3. 灰度发布 10% 流量，观察 p99 latency 与 error rate 15 分钟。\n"
        '{"service": "billing", "version": "2.3.1", "replicas": 6, "canary": {"weight": 10, "duration_min": 15}, '
        '"alerts": ["latency_p99 > 800ms", "5xx_rate > 0.5%"], "rollback": "auto"}\n'
        "4. If all SLOs hold, promote to 100% and tag the release in Git.",
    ),
    (
        "tk_code",
        "```python\n"
        "import asyncio\n"
        "from dataclasses import dataclass, field\n\n"
        "@dataclass\n"
        "class RateLimiter:\n"
        "    rate: float\n"
        "    burst: int\n"
        "    _tokens: float = field(init=False)\n"
        "    _last: float = field(init=False)\n\n"
        "    def __post_init__(self) -> None:\n"
        "        self._tokens = float(self.burst)\n"
        "        self._last = asyncio.get_event_loop().time()\n\n"
        "    async def acquire(self, n: int = 1) -> None:\n"
        "        while True:\n"
        "            now = asyncio.get_event_loop().time()\n"
        "            self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)\n"
        "            self._last = now\n"
        "            if self._tokens >= n:\n"
        "                self._tokens -= n\n"
        "                return\n"
        "            await asyncio.sleep((n - self._tokens) / self.rate)\n"
        "```\n"
        "Explain the token bucket algorithm implemented above and point out one potential bug.",
    ),
]

# Long, stable prefix used by the cache probe (well above the 256-token minimum).
CACHE_PREFIX = (
    "你是一个企业知识库助手。以下是《远航科技员工手册（节选）》，请严格依据手册内容回答员工的问题；"
    "手册未提及的内容请回答“手册中未说明”。\n\n"
    "第一章 工作时间与考勤\n"
    "1.1 标准工作时间为周一至周五 9:30 至 18:30，午休 12:00 至 13:30。\n"
    "1.2 员工可申请弹性工作制，核心在岗时段为 10:30 至 16:30。\n"
    "1.3 迟到 30 分钟以内不做处理，每月迟到累计超过 3 次需向直属主管说明情况。\n"
    "1.4 远程办公每周最多 2 天，需提前一个工作日在 OA 系统申请。\n\n"
    "第二章 休假制度\n"
    "2.1 法定年假：入职满 1 年 5 天，满 5 年 10 天，满 10 年 15 天。\n"
    "2.2 公司额外福利年假：所有正式员工每年 3 天，当年有效，不可结转。\n"
    "2.3 病假需提供二级以上医院证明，年度带薪病假 10 天。\n"
    "2.4 婚假 10 天，产假按国家及地方规定执行，陪产假 15 天。\n"
    "2.5 年假可结转至次年 3 月 31 日，逾期作废。\n\n"
    "第三章 报销与差旅\n"
    "3.1 差旅住宿标准：一线城市 600 元/晚，其他城市 400 元/晚。\n"
    "3.2 市内交通实报实销，单次打车超过 200 元需附说明。\n"
    "3.3 报销单需在费用发生后 30 天内提交，逾期不予受理。\n"
    "3.4 出差补贴每天 100 元，不含往返当天。\n\n"
    "第四章 信息安全\n"
    "4.1 禁止将公司代码上传至任何外部公共代码托管平台。\n"
    "4.2 办公电脑必须启用全盘加密并设置 10 分钟自动锁屏。\n"
    "4.3 发现安全事件须在 1 小时内向安全团队报告，邮箱 security@yuanhang.example。\n"
    "4.4 离职当天须归还所有设备并注销全部账号。\n\n"
    "第五章 学习与发展\n"
    "5.1 每位员工每年享有 5000 元培训预算，可用于课程、书籍与技术会议。\n"
    "5.2 参加外部技术会议需提前两周申请，会后一周内提交分享材料。\n"
    "5.3 公司每季度举办一次内部技术分享日，讲师可获得 500 元奖励。\n"
    "5.4 导师计划：新员工入职前三个月由资深员工一对一带教。\n\n"
    "第六章 绩效与晋升\n"
    "6.1 绩效考核每半年一次，结果分为 S、A、B、C 四档。\n"
    "6.2 连续两次 S 档可申请提前晋升评审。\n"
    "6.3 晋升评审每年 4 月与 10 月各一次，需提交自评材料与两位同事的推荐。\n"
    "6.4 C 档员工将进入为期三个月的绩效改进计划。\n"
)

CACHE_QUESTIONS = [
    "入职满 5 年的员工每年一共有多少天年假（含福利年假）？只回答数字。",
    "远程办公每周最多几天？只回答数字。",
    "内部技术分享日的讲师奖励是多少元？只回答数字。",
]

CACHE_EXPECTED = ["13", "2", "500"]

NEEDLE_TEMPLATE = "【重要记录】项目代号“{codename}”的紧急联络口令是：{secret}。"

FILLER_PARAGRAPHS = [
    "季度经营分析会议上，各部门依次汇报了业务进展。市场部指出线上渠道的获客成本较上季度下降了百分之七，但转化率略有波动，"
    "计划在下季度优化落地页并增加 A/B 测试频率。",
    "研发部门介绍了新一代数据平台的架构演进：从批处理为主转向流批一体，统一的元数据层减少了重复建设，实时指标的延迟从分钟级"
    "降低到秒级，为业务决策提供了更及时的支持。",
    "人力资源部通报了年度招聘完成情况，技术岗位的到岗率达到预期，管理培训生项目收到超过两千份简历，最终录取三十二人，"
    "将在九月统一入职并开始为期六个月的轮岗。",
    "财务部提醒各部门注意预算执行进度，上半年整体执行率为百分之四十六，略低于时间进度，下半年需加快重点项目的投入节奏，"
    "同时严格控制非必要的行政开支。",
    "客户成功团队分享了几个典型案例：一家制造业客户通过引入预测性维护模块，将设备非计划停机时间减少了三成；一家零售客户利用"
    "会员画像系统提升了复购率。",
    "法务与合规部门更新了数据出境的内部流程，要求涉及跨境传输的项目在立项阶段完成合规评估，并建立数据分类分级台账，"
    "确保关键信息基础设施相关业务符合监管要求。",
    "行政部宣布办公楼将于下月进行空调系统改造，施工期间部分楼层可能出现噪音，建议受影响团队合理安排远程办公；"
    "同时新的健身房与母婴室将在改造完成后同步启用。",
    "安全团队通报了最近一次钓鱼邮件演练的结果：整体点击率为百分之四点二，较上次下降明显，但仍有个别部门高于平均值，"
    "后续将对相关团队开展针对性的安全意识培训。",
]
