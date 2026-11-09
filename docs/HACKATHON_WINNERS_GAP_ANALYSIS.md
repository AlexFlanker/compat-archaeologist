# AIE Code Summit 2026 Hackathon：获奖作品公开资源分析，与 Compat Archaeologist 的硬性能力差距

写于 2026-11-08。读者是我们自己（Rollback Club 团队）。方法：主办方公告邮件 + 81 个 gallery 页面 + 9 个获奖仓库与 3 个 finalist 仓库全部克隆只读审阅 + 我们自己的仓库以外部评委视角审计，关键事实逐项回查原始文件（见文末"核对记录"）。视频只拿到了元数据，没有看过内容。

## 一句话结论

我们输的不是验证工程，而是三件硬性的东西：**没有一个评委能点、能看、三分钟内看懂的演示面**；**没有任何"系统随运行变好"的机制**（三条赛道之一，Fetch.ai 挑战也点名要求）；**赞助商产品对结论是装饰性的**（SubQ 没改变任何决策，Gemini 没用，而我们最依赖的 MongoDB 根本没有奖金赛道）。获奖者在这三点上全部做到了，而且每个数字都指向仓库里一个可打开的文件。

## 结果事实

- 官方公告（Cerebral Valley 邮件，2026-11-08 05:15Z）：总榜 1st Labbit / 2nd Lanes / 3rd Frankenstein；Meta 最佳 Airguard（$5k）；Fetch.ai 最佳 Lazarus / Foundry / Capy Kondo + Bob；SubQ 最佳 Airguard（$5k）/ Rosetta。81 个项目，6 个进入决赛（3 获奖 + 3 finalist：Sisyphus、Clean Room、Child1）。我们（gallery #26）未入围。
- 评审规则（登录页 participant guide，Frankenstein 把它原文抄进了仓库 `docs/EVENT_GUIDE.md`，已核对）：第一轮 3 分钟现场 demo + 1–2 分钟问答，**Technical Demo 35% · Implementation Difficulty 30% · Impact 20% · Creativity 15%**；前六名上台，四项等权。明令禁止"以 dashboard 为主要功能的项目"。
- 三条赛道原文要点：The System That Gets Better（每次失败变成版本化改动，held-out 验证后才采纳，否则回滚）；Software Archaeology（区分 observed 与 inferred，证明行为等价）；Trust but Verify（独立验证者，写自己的测试，可复现、难被操纵）。
- Fetch.ai 挑战的 6 条硬性要求：Agentverse 注册、实现 ACP、ASI:One 可发现可用、有意义的工具执行或多 agent 编排、**完整主流程在 ASI:One 对话内完成**、公开仓库。加分项：多 agent 协作、Payment Protocol、Interactive Cards、可靠性、实时数据、**赛后还能持续运行**。还必须通过 ASI:One Submission Agent 登记。
- Meta 和 SubQ 没有书面评审标准；MongoDB 和 DeepMind 没有独立奖项。
- 截至 2026-11-08 没有任何评委或赞助商公开解释获奖理由。以下判断全部来自作品本身。

## 获奖项目速览

| 项目 | 做什么 | 评委 10 秒内看到的"证明瞬间" | 赞助商用在哪（代码里真实存在） | 仓库硬指标 |
|---|---|---|---|---|
| Labbit (1st) | 浏览器 WASM MuJoCo 机械手实验台 + 可复现数据集工厂 | 公网 https://labbit.science 上 4–25 格实时物理仿真；HF 数据集公开（一天 569 次下载） | Muse Wonder 用严格 JSON schema 提出参数；SubQ Snap 看 4 帧图做"advisory"评审；无 Mongo/Fetch | 23 commits，27 pytest + 16 Playwright，README 398 行 4 个 GIF，SHA-256 贯穿分片/清单 |
| Lanes (2nd) | Pi coding agent 扩展：SubQ 按消息分 lane，大模型 prompt cache 不被打断；git 请求走本地蒸馏的 1.5B 模型 | 结果表：两条基线、重复运行、负结果（Google 上 +63%）；`distill/roster.json` 里 v1 采纳、v2 回滚的日志 | SubQ Snap 三种 typed question（lane 选择、scratchpad 门、git 意图）；Muse 三档模型；Gemini 做不同家族的独立评委 | 15 commits，0 测试，README 4 行，8.6k 行结果 Markdown |
| Frankenstein (3rd) | 从 GBA Doom 卡带字节级重写 C 源码 | 重建 ROM 与原版 **SHA-1 完全一致**（`lab/demo/union_rebuild.json`，已核对 235/568 函数），改一行 god-mode 代码变 200% 血量 | Muse 用 tool-calling 写每一行 C；SubQ Snap 在 384k 全程序状态上做 triage/升级/放弃/假匹配门；Gemini 第二意见；Atlas 存 idiom 库和事件；两个 Fetch agent 回答 Observed/Inferred | **311 commits，231 pytest + CI**，93 个 md，DECISIONS.md 时间日志，专门的 /judges 页按评审项逐条作答 |
| Airguard (Meta + SubQ 双冠) | 机场行李拖车控制器的部署前安全门：数字孪生 SFO + FAA 规则确定性裁判 | Muse 写的"快 20%"更新被 BLOCK：雾天超速、撞进 777 喷流；变异测试捕获率 56%→89%（`out/evals.json` 已核对） | SubQ 长上下文吃**未经摘要的 0.1s 原始轨迹**，单次 676k 直到 2.17M token（`out/calls.jsonl` 已核对）；Snap 在 370k token 上 5 秒内做 typed 判断；Muse 是被审的"作者" | 独立开发者，19 commits，无测试，但每次 LLM 调用缓存可离线重放，verdict 全输入哈希 |
| Lazarus (Fetch 1st) | 一句话生成一个有自己钱包的业务 agent，客户在 ASI:One 里下单付款 | **链上真实 testFET 转账**（4/6 笔 tx 在 dorado REST 核实，含 agent 钱包到 agent 钱包） | 6 个 uAgent + 每业务一个 agent（32 个），6 种 card，`@agent.paid` 付费墙链上校验；大部分 Fetch 管线来自第三方 `fetch-hackkit` | 5 commits（1 个在赛前，README 已披露），107 tests，9 张截图，committed evidence |
| Foundry (Fetch 2nd) | ASI:One 内的 agent 工厂：搜 Agentverse、现场测试已有 agent、不合格就造一个并发布 | 发布的 ISBN agent 在 Almanac 上确实 active；Proof Passport；拒绝发布 0/11 的 2048 agent | Foreman + 15 role agent（7 个为 mailbox）；手写 ACP + Payment Protocol（Stripe test）；Agentverse hosting API 全链路（创建/上传/启动/删除/可发现性轮询） | 40 commits，142 tests，mermaid 图；**但 evidence/ 为空，所有数字 only-asserted** |
| Capy Kondo (Fetch 3rd) | 一张照片→所有物品的诚实定价上架→ASI:One 内搜索砍价付款 | 照片上实时 SAM 轮廓和价签；SubQ 判"品牌可见 0.06"→去品牌重新定价 | Muse + SAM 3.1；Gemini Argon/Nano Banana；SubQ 五个决策点，**代码里写死阈值**；2 个 agent + Payment Protocol + 自定义 A2A 协议 | 13 commits，无测试，无 limitations；lessons 以 git commit 版本化 |
| Bob (Fetch 3rd) | 改 coding agent 的配置层，在**新鲜随机 held-out seed** 上证明后才开 PR | 公开仓库里真有一个 PR，正文是"Sandbox certificate: proven, 15 fixed / 0 broken / 85 unchanged, 20 fresh seeds"（`gh pr view` 已核对） | 5 个 mailbox agent，官方 card validator，8 张 card 的流程；SubQ Snap 选层和风险 | 2 commits（整包一次推），**143 pytest**，7 条 known limitations + 10 条 roadmap |
| Rosetta (SubQ 2nd，与我们同赛道) | 只给压缩后的 JS，跑数百次→Muse 提规则并设计**反证**实验→人类裁决 quirk→Muse 写 TS 移植→500 个 held-out 验证→vitest 契约 | Monaco 里**每一行按证据强度着色**（绿/黄/红/紫）；改一个数字契约就挡下并指出破坏的规则 | SubQ Archivist 一次读整个 bundle + 全部观测 + 规则历史（8× 主 agent 上下文，~150 个 typed question）；6 uAgent + 公网 agent，**消息 secp256k1 签名服务端校验**；Atlas 18 个集合做证据账本并驱动回放 | 51 commits，无单元测试，16 张截图，`out/` 被 gitignore，数字不可复现 |

## 我们的硬性能力差距（按优先级）

### 1. 演示面：评委看到的是终端和 388 行 Markdown

- 我们：`README.md` 第 19 行"There is no dashboard"；git 里没有任何图片或 GIF；60 秒视频的描述是"a real run of ./run_demo.sh, sped up"，ElevenLabs 配音，1 次播放。Interactive Cards 只有决策卡在 ASI:One 里验证过，其余"haven't been seen in ASI:One"（`docs/DEMO_GUIDE.md`）。
- 获奖者：Labbit 有公网 URL 和实时 WASM 物理；Frankenstein 现场"按回车"两个 agent 挖掘、裁判 0.1 秒盖 MATCH 章、Doom 在屏幕上跑、手机扫码玩；Airguard 有 three.js 机场和撞喷流的 GIF；Rosetta 有逐行着色的代码视图和事件回放滑块；Capy 有照片上的实时轮廓。
- 规则本身的提示：dashboard 被禁，但所有获奖者都有"**暴露过程的界面**"：不是指标面板，而是把裁判的工作可视化。Frankenstein 的 playbook 原话："Put the judge inside the demo"、"Never call the UI a dashboard"。
- 技术演示占 35%，这一项我们几乎是零分起步。

### 2. 没有"系统随运行变好"的机制

- 我们：`archaeologist/tco.py` 的缓存是为了"a demo run gives the same numbers"；没有版本化规则、held-out 集、采纳/回滚判据。scoreboard 的 R7 正则是看完 Muse 输出后**手工**改的（`archaeologist/scoreboard.py` docstring 有披露），这是自我改进的反面。
- 获奖者：Lanes 的 `distill/roster.json`（teacher 0.65 → base 0.475 → v1 1.0 adopted → v2 0.875 rolled_back，带时间戳，已核对）；Airguard `improve.py` 只在 held-out 捕获率上升且误拦不升时采纳，结果三轮全部拒绝并如实写进 README；Capy 每条 lesson 一个 git commit，带 before/after 百分比；Bob 的 PR 证书；Frankenstein 的 idiom 库有采纳/退役规则，测出来没有超过噪声就直接说"我们不声称它有帮助"。
- 这条赛道是三分之一，Fetch.ai 挑战也把"getting better over time"列为第一项。我们在 `docs/DEMO_GUIDE.md` 里只认领了另外两条。

### 3. 判定不是二进制、不可哈希，钱的结论依赖假设

- 我们：`archaeologist/tco.py:288` 的 `_decision` 只看 `net > 0` 和 blocking gaps（已核对）。第一步 −$26,700 = Muse 估的 160 小时 × $120 + 0.5 × $15,000（`assumptions.yaml`）。ShopLog 是当天早上写出来、故意依赖 MongoDB 特性的应用，FerretDB 文档本来就写着事务未实现。评委会读成"在自己写的数据上确认了一个已知限制"。
- 获奖者：Frankenstein 的证明是**编译器字节相等**和 **ROM SHA-1 相同**；Airguard 的 verdict 把候选代码、每个上下文文件、FAA 文本、地图、prompt 全部哈希进 `verdict_hash`，`selfcheck.py` 断言重跑哈希一致；Bob 用 Valkey 做字节级 oracle。LLM 只提议，Python 决定。
- 我们其实有同类的好东西：MongoDB-as-oracle 的 probe 门（`archaeologist/probe_runner.py:156`，30 个 probe，9 次被 MongoDB 纠正，0 个剔除）。但它埋在 README 的"Design choices"里，不是头条。

### 4. 没有"验证验证者"的证据：held-out、植入缺陷、噪声带

- 我们做到的：cutover drill 注入 CDC 缺口和删索引，验证器抓到 2/2，21/27 文档；MongoDB 对自己 30/30 自检；control app 得 GO。这些是好的。
- 我们缺的：没有从查询日志里封存一片 held-out 从不给模型看；没有"植入 N 个不兼容，probe 抓到 N/N，误报 0"的表；没有重复运行的噪声带；延迟数字在三个地方写成 ~7→4000 ms、470×、537× 三个版本。
- 获奖者：Rosetta 固定种子 500 个 held-out、泄漏阻断、"unseen-only"子分数、3 个植入 bug 全部抓到、伪造签名/篡改测试；Frankenstein 私改 11 处的变体 + 记忆探针（纯记忆 0/20）+ 39 次假声明被抓的计数器；Airguard 变异测试按层报告；Lanes 基线跑两次说明 12% 的 run-to-run 差异。

### 5. 赞助商产品对结论是装饰性的，且用在了"可替代"的地方

- SubQ：`logs/subq_calls.jsonl` 59 次调用，但 SubQ 的概率只是"a separate second opinion on the same facts"（memo 原话）；唯一单源条目 `X:graphLookup` 0.854 在 FerretDB 上是 equal，**SubQ 在两次运行中没有改变任何决策**。">0.999 → <0.001" 的翻转，是把 `net_12_months_usd` 直接放进 state 再问模型，结果是必然的。
- Meta：是核心（代码证据、probe、工时估算），但 `logs/meta_calls.jsonl` 里相当比例返回无效 JSON 后重试（审计读到 22/71，我粗查 288 行里 62 条标记异常；比例不精确，方向成立）。
- MongoDB：MCP 的 183 次调用里 180 次是 `explain`，pymongo 能做同样的事；Atlas 是结果存储加一次搜索索引读取；Performance Advisor 从未采到（`advisor_available: false`）。Gemini 完全没用。
- 获奖者的共同点：把赞助商模型用在**非它不可**的地方，并给每个模型一个可度量的工作。Airguard 给 SubQ 的是无法摘要的原始轨迹（676k–2.17M token），Snap 的判断与确定性裁判逐条比对一致率；Rosetta 用 Snap 做 ~150 个 typed question 并在代码里用阈值行动（none ≥ 0.5 → 未解释，contra ≥ 0.7 → 挑战）；Capy 的五个 SubQ 决策点阈值全在代码里（brand_visible < 0.35 → 去品牌重定价）；Frankenstein 让 Muse 写每一行代码。每次调用的 token、延迟、成本都写进日志并在 README 的 how-it-works 表里按步骤标注模型名。
- 战略层面：MongoDB 没有奖金赛道。我们把最多的工程投入放在了唯一不发钱的赞助商上。

### 6. Fetch.ai 集成没有达到"完整流程 + 可持续运行"

- 我们：mailbox 间歇性 401 和连接失败（`logs/agent.log`）；Almanac 合约注册因无资金未完成；ASI:One Submission Agent 登记在 2:55 PM 还是"To do"；没有 Payment Protocol（"Not built — Don't claim it"）；5 种 card 只有 1 种在 ASI:One 里看到过；`uagents` 不在 `requirements.txt`；agent 只在笔记本开着时可用。6 条硬性要求里第 3、5 条存疑。
- 获奖者：Lazarus 的链上 FET 转账和"只有链上确认才算 paid"；Foundry 用 Agentverse hosting API 发布的 agent 现在还在线；Bob 的 card 流程以一个真实 GitHub PR 收尾；Capy 两个 agent 间有自定义协议 + Payment Protocol；Rosetta 把 6 个 agent 的每一步用 secp256k1 签名并在服务端校验。共同点：**card 链有一个终点动作**（收据、PR、发布、付款），且效果被验证并展示。

### 7. 仓库卫生与可复现性：我们的优势和漏洞并存

- 优势（要保留并放大）：所有头条数字都对应 `out/` 里被 git 跟踪、可重新生成的文件（+$20,124 → −$98,823、6/23、21/27、10/10、48/48 全部核对一致）。这点比 Rosetta（`out/` 被忽略）、Foundry（evidence 为空）、Lanes（runs 被忽略）都强。密钥卫生也好。
- 漏洞：没有 LICENSE、没有 CI；测试只覆盖 normalize/classify/cards，产出数字的 cutover、replay、ledger、tco 零测试；赛后重写的 agent（`fetch_agent/router.py`、`answer.py`、`archaeologist/evidence_pack.py`、4 个测试文件）至今未提交，评委没看到；内部文档 `docs/HANDOFF.md`（"Blocked: Meta key not in .env yet"）和 "Left to do" 公开可见；26 个 commit 里 24 个一人在 5 小时 19 分钟内完成。
- 对照：Frankenstein 231 个测试 + CI + `tools/counts.py` 一条命令重算官方数字；Bob 143 个测试；Foundry 142 个。

### 8. 叙事：头条被埋，概念过载，赛道没点名

- 我们的 README 开头是 120 词的虚构背景故事，产品是什么到第 13 行才出现；+$20k → −$99k 的翻转不在 README 里；评委要同时理解约 30 个概念（ledger、oracle、CDC、TCO、reversibility、SubQ Snap/Preview、MCP、Atlas Search、ASI:One……）加 F1–F11、R1–R10、B01–B50。"one-page brief" 后面跟 300 行附录。
- Gallery 简介 103 词，有钱的数字和机制句，但没点名赛道，没有 held-out / 独立裁判 / 回滚这类句子，没有证据计数。获奖者简介长短不一（32 到 397 词），共同点是：一句机制句（X 写、Y 判、Z 证）、至少一个 before/after 数字、精确的赞助商模型名、一句"agent 碰不到裁判"。
- Frankenstein 的 /judges 页按四个评审项逐条回答并给出每个数字的来源文件；Airguard、Lazarus、Bob、Rosetta 的 README 都有 "Honest limits" 表和 "How we checked the checks"。

## 我们真正领先的地方（对比要公平）

- MongoDB-as-oracle 的 probe 门和修复循环，是全场少见的"让模型先预测再由真系统裁决"的机制，有日志可查。
- 独立的 cutover 验证器在**干净**迁移里发现真实丢失（大小写不敏感唯一索引、$jsonSchema validator），这比注入故障更有说服力。
- control app 作为公平性检查，防"MongoDB 营销"质疑。
- 每个估算都标注来源，"unavailable" 而不是编数字；grader 修改有披露。
- 所有产出物进 git 且可再生成。

## 打磨清单（按投入产出排序）

1. **一个暴露过程的界面，不叫 dashboard。** 每条 finding 一页：谓词、支持它的 run ID、OBSERVED/INFERRED/DISPROVEN 状态历史、"Challenge" 按钮重新跑 probe。查询日志按证据强度着色（照 Rosetta 的 Monaco 视图）。视频里要出现这个界面，不是终端。
2. **可哈希的 verdict pack + 一条命令重算所有数字。** 把 probe 集、replay 输入、assumptions、FerretDB 镜像摘要全部哈希进 verdict；加 `tools/counts.py` 式脚本；统一延迟数字的三个版本。
3. **封存 held-out 查询片 + 植入不兼容的变异测试。** 从 `evidence/query_log.jsonl` 固定种子抽一片从不给 Muse 看；植入 N 个已知不兼容（例如去掉 collation、改事务语义），报告"抓到 N/N，误报 0"，并重复运行给出噪声带。
4. **一个真实的自我改进循环。** 把 probe 生成策略/prompt 版本化；新版本只有在 held-out 片上捕获率上升且误报不升才采纳，否则回滚；把 roster/history 文件提交到仓库，哪怕结果是"没有采纳"，像 Airguard 一样写进结果表。
5. **让 SubQ 和 MongoDB 做非它不可的事。** SubQ 长上下文读完整未摘要的 oplog/profiler 日志（我们有 1,554 条操作和整份 oplog），输出由代码按阈值行动并显示阈值；Snap 的判断与 MongoDB 裁判逐条比对一致率。MongoDB 方面用 Atlas 做可回放的证据账本而非结果存储。记录每次调用的 token、延迟、成本。
6. **Fetch.ai 补齐硬性要求。** 托管或常驻的 agent；card 链以一个被验证的动作收尾（例如"批准 Stay → 签名收据写入 Atlas，返回哈希"）；接 Payment Protocol（test 模式即可）；完成 Submission Agent 登记；`uagents` 进 requirements。
7. **README 重写为一屏。** 第一行机制句 + 赛道名；第二块结果表（每行一个来源文件）；第三块"我们如何检验检验器"；限制与披露；截图；赞助商表（模型名 + 它控制什么 + 一个数字）。把 HANDOFF 这类内部文档移出或标注。
8. **卫生。** LICENSE、CI、为 tco/cutover/ledger 补测试、提交赛后工作、删掉不一致数字。
9. **演示设计。** 15 秒内出现产品画面；一个评委能复述的锚句；一个现场"按回车"的真实运行段（哪怕只跑 2 个 probe）；Q&A 预案里准备"这不就是 MongoDB 营销吗"和"你们的应用是自己写来失败的吗"。

## 未核实与不确定点

- 所有视频都没看过，只有标题、时长、发布时间。Lanes 的 Claude artifact 演示页无法读取。
- 没有任何评委或赞助商的公开解释；"为什么赢"是从作品倒推的判断。
- Lazarus 有一个赛前（11-06 22:23 PST）的 commit，README 有披露；多个入围仓库有截止后的 commit。"只算当天工作"的规则显然是靠 demo 而非 git 执行的。
- Clean Room 简介里的"31/31、100%"在 committed 的 `copies/scores.json` 里找不到（agent 报告，我未复核）。
- 我们 Muse 无效 JSON 的精确比例未定：审计说 22/71，我粗查 288 行有 62 条异常标记，口径不同。

## 核对记录（主线程回查原始文件，不依赖 agent 转述）

| 事实 | 回查位置 | 结果 |
|---|---|---|
| 评审权重 35/30/20/15，dashboard 禁令 | `repos/frankenstein/docs/EVENT_GUIDE.md` L24, L36–41 | 一致 |
| Fetch.ai 六条硬性要求 | Fetch 挑战 Google Doc 文本 | 一致 |
| Airguard 56%→89%，最大 SubQ 调用 2.17M token | `airguard/out/evals.json`, `out/calls.jsonl` max prompt_tokens 2,174,543 | 一致 |
| Frankenstein ROM SHA-1 相同，235/568 | `lab/demo/union_rebuild.json` identical=true, recovered=235, from_original=333 | 一致 |
| Frankenstein 293 commits 当天 + CI | git log, `.github/workflows/ci.yml` | 一致 |
| Rosetta `out/` 被 gitignore，16 张截图 | `.gitignore`, find *.png | 一致 |
| Lanes roster v1 1.0 / teacher 0.65 / base 0.475 | `lanes/distill/roster.json` | 一致 |
| Labbit 线上健康检查、HF 数据集 | `labbit.science/api/health`, HF API | 一致 |
| Bob PR #3 Sandbox certificate | `gh pr view 3 --repo diyajh/bob-worker` | 一致 |
| 我们：README L19、`_decision` 逻辑、无 LICENSE/CI、未提交的赛后工作 | 本仓库 | 一致 |

## 来源

- 获奖公告：Cerebral Valley 事件邮件 2026-11-08 05:15Z（Gmail）；gallery https://cerebralvalley.ai/e/aie-code-2026-hackathon/hackathon/gallery
- 事件页 https://cerebralvalley.ai/e/aie-code-2026-hackathon ；Fetch.ai 挑战文档 https://docs.google.com/document/d/1z7ybvx9I80KI-n4DEieN9i11Zoj4ki4TTvtUWqnULQQ
- 仓库：Labbit https://github.com/nayeonshin/aie-code-summit-2026-hackathon · Lanes https://github.com/SomeoneElseSt/aie-hack · Frankenstein https://github.com/KarthikSubramanian07/Frankenstein （评委页 https://tryfrankenstein.pages.dev/judges）· Airguard https://github.com/roshni2020/airguard · Lazarus https://github.com/Kush614/Lazarus · Foundry https://github.com/Tirth2530/Foundry · Capy Kondo https://github.com/chinesepowered/hack-aie · Bob https://github.com/ArjunHaldiya/bob （worker https://github.com/diyajh/bob-worker）· Rosetta https://github.com/KamayaniR/rosetta · Sisyphus https://github.com/aadityad12/Sisyphus · Clean Room https://github.com/Shri2007/cleanroom · Child1 https://github.com/a8nova/child-1
- 线上：https://labbit.science · https://huggingface.co/datasets/MedSalim/labbit-robotic-manipulation · https://airguard-ecru.vercel.app
- 我们：https://github.com/AlexFlanker/compat-archaeologist · gallery #26 https://cerebralvalley.ai/e/aie-code-2026-hackathon/hackathon/gallery/26
- 六份逐项分析报告（agent 产出，含文件路径和引文）在本机 scratchpad：`/private/tmp/claude-501/-Users-tianjiesun--claude-spares-spare-XSAPlc/f040b6ea-6c3d-43f3-ab92-ab2333fe017f/scratchpad/agents/`
