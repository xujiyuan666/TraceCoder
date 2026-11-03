# revfactory/harness 深度解读 —— 它和 TraceCoder 是栈上的两层

核对时间：2026-10-28。源文本全部下载到 `C:\Users\EDY\AppData\Local\Temp\harness-src\`（19 个文件，约 198KB 正文）。
本文每一条关于 harness 的判断都注明是我一手读到的，还是子代理全文读取后转述的。

---

## 0. 一句话结论

harness 不是 agent，是**写给 Claude Code 的编排规格**：18 个 markdown 文件教模型"如何给一个领域设计一套 agent 团队"，它自己不调模型、不执行工具、不管上下文、不认权限。
TraceCoder 是**同一件事的运行时实现**：30,054 行 Python 把 loop、工具、权限、事实存储、压缩真正跑出来。

所以正确的比较不是"谁功能多"，而是：**同一个工程本能（可重放、可审计、确定性优先），一个写在 prompt 里靠模型自觉，一个写在代码里可以断言。**

---

## 1. 客观形态（我一手核）

| 项 | 值 | 来源 |
|---|---|---|
| 自述 | A meta-skill that designs domain-specific agent teams, defines specialized agents, and generates the skills they use. | GitHub API `description` |
| 版本 | 2.1.0（plugin.json / marketplace.json / CHANGELOG / README badge 四处一致，且这是 2.1.0 的一条显式修复项） | 我 grep |
| 热度 | 9,274 star / 1,333 fork / 3 open issue / Apache-2.0 | GitHub API |
| 时间 | 建库 2026-03-26，最后推送 2026-09-28（≈1 个月未动） | GitHub API |
| 语言 | GitHub `language` 字段 = **None**（linguist 检测不到代码） | GitHub API |
| 全树 | **37 个条目** | git trees API |
| 真实资产 | `skills/harness/SKILL.md` 35.7KB + `skills/evolve/SKILL.md` 6.7KB + `references/` 10 篇（≈120KB）+ 2 个 plugin json | tree + 我下载后 `wc -c` |
| 其余体积 | 21MB 全是演示媒体：`harness_reel_en.gif` 7.8MB、`harness_reel_ko.gif` 7.5MB、`docs/harness-animation.mp4` 6.2MB | tree sizes |
| 测试 | **0 个测试文件** | 全树列举 |
| CI | **没有 `.github/workflows/`**（contents API 404，`.github/` 下只有 issue/PR 模板） | 我核 |
| 正文语言 | README 有 EN/KO 双语；**SKILL.md 与 10 篇 references 正文只有韩文** | 我读 SKILL.md 全文 |

`plugin.json` 只声明了 `name, description, version, author, homepage, repository, license, keywords` —— **没有 commands / skills / agents / hooks / allowed-tools，也没有任何权限声明**。skills 靠目录约定被发现。由此产生一个真实缺陷：README 宣传的 `/harness:evolve` 并不是声明出来的命令，而 README 给的备用安装方式 `cp -r skills/evolve ~/.claude/skills/harness-evolve` 会把 skill 改名，使那条 slash 命令失效。（子代理发现，我核对了 plugin.json 字段与 README 第 78-88 行的安装段落，中~高置信。）

---

## 2. 它的机制到底是什么（SKILL.md 全文拆解）

### 2.1 六条核心原则（第 10-17 行）

1. agent 定义进 `.claude/agents/`，skill 进 `.claude/skills/`；**agent 写"谁来做"，skill 写"怎么做"**，两者不许混。
2. 按工作流形状选执行模式：顺序和重复条件能用代码定 → workflow 编排；要跟同一个专家来回交换意见 → 持续型 agent；只要一次结果 → subagent 委派。
3. 按复杂度/工期/自主性/响应速度选模型：规划+长程自主 → `fable`，设计/代码生成/复杂分析/交叉验证 → `opus`，日志解析/格式转换/简单采集 → `sonnet`。
4. 新会话要能找回编排器，所以 `CLAUDE.md` 里只留**调用条件 + 变更历史**。
5. 执行中学到的东西持续回灌 agent/skill/CLAUDE.md，这件事交给 `harness:evolve`。
6. **产物语言跟用户对话语言走**，明确写着"不要因为本 skill 文档和 references 模板是韩文，就把产出发成韩文，模板的标题和示例也要翻译过去"。

第 6 条是这个仓库里我最喜欢的一处设计：作者清楚自己是韩文模板，并主动切断了模板语言对产物的污染。

### 2.2 Phase 0-7 流程

| 阶段 | 干什么 | 值得注意的具体规则 |
|---|---|---|
| 0 现状审计 | 读 agents/skills/CLAUDE.md，分"新建 / 扩展 / 运维"三条路 | 一张"变更内容 × 哪些阶段可省略"的表（第 31-35 行）；发现 `TeamCreate`/`TeamDelete`/实验 flag 即判定 v1 产物并提议迁移；把实际文件清单与 CLAUDE.md 记录对账找不一致；**报告并等用户确认才继续** |
| 1 领域分析 | 拆生成/验证/编辑/分析；扫技术栈、数据模型、关键模块 | 4 个判别问句：清单能否预先枚举？是否需要反复修检？是否需要 agent 间交换意见？是否需要跨轮记忆？<br>第 52 行：按用户水平调术语难度 —— **不要对新手直接抛 assertion / JSON Schema 这类词** |
| 2 模式+队形 | 三模式选一或按阶段混合；六形态选一 | 决策顺序写死在第 68-71 行：**能预先用代码表达 → workflow，"不要把既定流程交给模型临场判断"**；否则需往返/记忆 → 持续型；否则 → 一次性委派。<br>用 `Workflow` **必须用户明确同意**；默认小规模，只有用户说"彻底/全量"才放大（第 73 行） |
| 2-2 质量模式 | 五种可组合的验证器 | 敌对验证：每 findings 配 N 个验证 agent，**过半 `confirmed` 才通过**，`refuted`/`uncertain` 不计入通过；评审团；loop-until-dry（连续 K 次新发现为 0 才停）；多基准并行探索；缺漏审查员专门只找遗漏 |
| 3 agent 定义 | 写 `.claude/agents/{name}.md`，frontmatter 必填 name/description，可选 tools/model | 只读评审 agent 从 `tools` 里去掉 Edit/Write；**要改产物的必须同时给 Write 和 Edit**，理由（第 133 行）是："只有 Write 时改一行要重写整份文件，产物的稍微一大，agent 就放弃修改、走捷径"。<br>同一行还承认一个宿主 bug：**`tools` 里声明了却未被注入**，所以要求持续型 agent 在首次汇报里自陈实际可用工具 |
| 4 skill 编写 | `SKILL.md` + `scripts/` + `references/` + `assets/` | description 要写清"什么时候用"和"看着像但不该用的情况"，给了 PDF 的坏例/好例对照（第 170-172 行）；五条原则：先讲理由不要只列 ALWAYS/NEVER、<500 行、按原理泛化不要写只挡单个例子的规则、重复代码进 `scripts/`、指令语气统一；渐进披露时点表：元数据 always（约 100 词）／正文 on invocation（<500 行）／references as needed（无限制）／scripts 可**不读内容直接执行** |
| 5 编排落地 | 三套 orchestrator 模板 A/B/C + 数据通道 + 错误处理 + 规模 | 五种数据通道各有适用条件（结构化返回／普通返回／消息／共享任务列表／文件）；文件约定 `_workspace/{phase}_{agent}_{artifact}.{ext}`，**中间产物留在 `_workspace/` 供事后验证**；错误处理 5 条（见 2.3）；规模表：小 2~3 持续型/2~5 次调用，中 3~5/~10（超并发上限自动排队），大 监督者+3~5 工人/数十~数百，**全局上限 1,000 次调用**；`budget.remaining()` 对应用户写的 `+500k`；后续请求分三种（部分重跑／新输入先把旧目录改名加时间戳／无 `_workspace/` 从头跑），有上一轮 `runId` 用 `resumeFromRunId` |
| 6 验证 | 六小步 | 结构检查（含"别在 `.claude/commands/` 里造东西"、v1 残留清零）；按模式检查（`meta` 只准字面量、禁 `Date.now()`/`Math.random()`、只在必须等全量结果时用 `parallel()`、`.filter(Boolean)` 不能漏、`phase()` 标题要和 `meta.phases` 一致）；执行测试（每 skill 2~3 条真实请求，**With-skill vs Baseline 同一消息并行跑**，定性 + assertion 定量双轨）；**触发测试 10 条该触发 + 10 条不该触发的 near-miss**，并明确说"明显无关的请求测不出边界"，举例：测图像生成 skill 不该用"写斐波那契"，该用"把 excel 里的图表导出 png"（结果像图像任务、实际该用表格工具）；干跑；把测试场景写进 orchestrator（1 正常 ≥1 错误） |
| 7 运维+evolve | 本 skill 直接处理巡检/逐项修改/变更历史/改后验证 | 提议转 `harness:evolve` 的三个信号：**同类反馈 ≥2 次**、同一原因反复失败、**用户绕过 orchestrator 手工做同一件事** |
| 末尾 | 16 项自检清单 | 含"按任务性质选了每个 agent 的 model 并写了理由，且没有给所有 agent 批量钉同一个高性能模型" |

### 2.3 错误处理那 5 条，是全文最像"工程"的一段

（SKILL.md 第 262-266 行，我逐条读过原文）

1. 一次重试仍失败 → **带着缺失继续**，但必须在最终报告里写明缺了什么；互相冲突的数据不许删，要连来源一起留着。
2. 配额耗尽／认证过期／权限拒绝这类"重试结果必然相同"的失败 → **不许重试，重试只会消耗剩余配额**；改为打开中间产物人工核对实际跑到哪、把缺失记成文件、并告知配额恢复时间。
3. 编排器替卡住的 agent 填空时，**只能填自己直接核实过的事实，不许猜那个 agent 的判断**（例如它为什么排除某份资料）——原文写"用猜测填就是伪造事后验证要用的记录"。
4. workflow 里失败的 `agent()` 返回 `null`，且 **`parallel()` 不抛异常** → 必须 `.filter(Boolean)`，并用 `log()` 记录缺失条数。
5. 持续型 agent 不回话 → 先 `SendMessage` 问状态再重令；仍失败就**换个名字重启同一个类型**，并把必需上下文用 prompt 传过去。

### 2.4 我 grep 确认存在的具体数值

`.filter(Boolean)`（7 个文件都出现）、`Date.now()` 禁令（3 个文件）、`resumeFromRunId`（6 个文件）、`budget.remaining`（3 个文件）、`1,000` 调用上限、`50_000` token 阈值、`judgeCount`（默认 3）、`dryRuns`（默认 2）、`shasum`（冻结校验）、`TeamCreate`/`EXPERIMENTAL_AGENT_TEAMS`（v1 残留词，10 个文件）、`haiku` **0 次**（只有三档不是四档）、`500 줄`（SKILL.md 行数上限）。

---

## 3. 真正硬核、值得你抄的东西

1. **确定性优先选模式**：能写成代码的控制流绝不交给模型临场判断。这句话就是你项目里 `AgentLoopLimits` 和 stop reason 存在的同一个理由，只不过 harness 把它当成"选模式的标准"，你把它当成"代码里的检查"。
2. **失败要分类，不能一律重试**：配额/认证/权限类失败重试只会烧钱。你的 `agent/provider_retry.py` 分的是可重试 provider 错误，这条是同一层语义，可以对照看有没有覆盖"重试会更贵"这一类。
3. **不许用猜测填补停住的 agent**——"猜测填充＝伪造审计记录"。这条和你的证据分级洁癖完全同构，可以直接引用它作为行业侧说法。
4. **`.filter(Boolean)`**：并发批次不抛异常而是留 null，必须显式过滤并记缺失数。这是踩过坑才写得出的规则。
5. **禁 `Date.now()`/`Math.random()` 是为了 resume 缓存能命中**：可重放要求脚本是纯函数式的。你项目的对偶实现是 JSONL replay（`context/runtime_replay.py`），你的做法更强——因为不依赖宿主给你 memo。
6. **触发测试要 near-miss 而非明显无关**：这是判别效度问题，和你做医学基准时坚持"留出集/注入改写去重"是同一个方法论。这是它最值得搬进 TraceCoder 的一条。
7. **QA 检查项必须是"对照断言"而不是"存在性检查"**（`qa-agent-guide.md`）：把"这个 API 端点存在吗"改成"API 响应结构和对应 hook 的类型一致吗"；它的核心论点是**主要 bug 类是边界/契约不一致**（两边各自都对，接起来错：`{projects:[...]}` vs `Project[]`、`thumbnailUrl` vs `thumbnail_url`、文件路径 vs `href`、状态机声明 vs 实际写入）。报告分 pass/fail/**unverified** 三档。这一段经验密度最高。
8. **按任务性质而非 agent 地位选模型**，四条规则（`model-selection-guide.md` 第 117-120 行，子代理转述）：不因"这个 agent 重要"而选贵模型；**无理由批量钉同一模型＝只增成本**；编排层用 fable 不代表下层 worker 也用 fable；难判断时默认 sonnet。并且明确否认线性等级——"fable 并非无条件比 opus 推理更深"。

---

## 4. 必须打折的部分（附证据强度）

### 4.1 热度构成（高置信，可核验）
9,274 star 对应的是：3 个月存量、**0 个测试**、**没有 CI**、正文只有韩文、21MB 演示视频。它是一门把"品类词 + 演示"做到极致的生意（topics 里直接写 `harness-engineering`）。这不是贬义——说明叙事与分发是独立能力——但**不能当作工程质量证据**。

### 4.2 README 的自测数字（我一手核，逐字）
> A controlled A/B on 15 software-engineering tasks ... mean quality 49.5 → 79.3 (+60%), 15/15 win rate, −32% output variance (**n=15, author-run**) ... **Treat these as author-measured numbers; run your own pilot** for adoption decisions.

两面看：
- 值得学：它在同一行里主动标了"作者自测、请自己跑试点"，没有伪装成社区基准。这是你要求的证据标注纪律的正面样本。
- 要打折：n=15、作者自测、无盲评协议、无评分细则、结论指向的是**另一个仓库** `revfactory/claude-code-harness`（v1，我确认该仓库存在），测的是"结构化预配置"的效果——**和 v2 的三模式、模型分档、evolve 不是同一个对象**。15/15 全胜在这种样本量下不具备区分力。

### 4.3 evolve 的"可度量变好"是宣称，不是度量（子代理全文读取，中高置信）
原文说"让下一次执行可度量地变好"，但它的校验步骤只有结构性项目（frontmatter/引用一致、触发项 3+3、与 CLAUDE.md 一致），**不重跑 A/B、不做跨代数值对照**；"同类请求 ≥2 次"这个阈值也没有任何计数来源。更糟的是它自己前后不一致：evolve 要求 **3+3** 触发用例，testing-guide 要求 **10+10**。

### 4.4 它的方法论软肋：全靠模型自觉（子代理，中高置信）
- 盲评只靠 prompt 里一句"为守匿名不要打开 `eval_metadata.json`/`with_skill/`/`without_skill/`"——**没有沙箱，模型完全读得到**；`withSkillSlot` 的交替也依赖主 agent 守纪律。
- `timing.json` 里的 `total_tokens`/`duration_ms` 由 agent 在通知时刻**手抄写入**，文档自己写"事后无法恢复"。也就是说 A/B 的成本数据是可编造的。
- `pass_rate` **没有任何目标阈值**，终止条件之一是"用户满意"→ 不可复现。
- "加载本 skill 即视为同意使用 Workflow"是策略措辞，运行时并不强制。

对照你的项目：TraceCoder 的 `PrewriteReview` 会在真正调度前**重验文件快照并阻止过期预览**，pending 的原始 tool_call 只信本地会话状态不信模型回放。同样是"不许蒙混"，一个是文字劝告，一个是代码拦截。

### 4.5 示例没有兑现规则（子代理 grep，中置信）
`team-examples.md` 五个例子（综合调研／科幻小说写作／代码评审／大规模迁移／网漫制作），其中两个含可直读的 JavaScript（`FINDINGS`/`VERDICT`/`MIGRATION_RESULT` schema、`pipeline`/`parallel`/`isolation:'worktree'`），三个是 ASCII 伪流程。但**没有一个例子给出真实生成的 `.claude/agents/*.md` 正文**，`researcher.md`/`fact-checker.md`/`migrator` 全是名字占位；并且五个例子里**模型档位出现 0 次**——没有示范它自己第 3 条核心原则。

### 4.6 最大的结构性风险：宿主 API 一变，它就静默退化（v1→v2 就是证据）
v1 建立在 `TeamCreate`/`TeamDelete` + `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` 上；这些 API **已被移除**，v2 只能 ground-up rebuild 并写迁移文档。迁移文档里那句故障描述是全文最重要的一行（中置信，子代理转述）：

> v1 的 orchestrator 直接跑会"尝试调用不存在的工具，然后**静默退化为单 agent 执行**"（CHANGELOG 2.0.0 称之为"实质已坏"）。

这就是 prompt-as-code 的失败模式：**它不报错，它只是悄悄不工作了，而输出看起来仍然像成功。**
它还观察到宿主的一个不一致：Claude Code 2.1.226 里 `TaskCreate`/`TaskUpdate` 声明在 `tools` 中却没被注入，`SendMessage` 却被注入，"边界不清楚"。

你项目的对偶答案是：护栏命中会产出显式 stop reason（`tool_round_limit`/`provider_call_limit`/`turn_timeout`），权限拒绝会落成一条 tool 消息，一切进 JSONL——**退化是可断言的**。这一句可以直接当你面试的回答骨架。

### 4.7 模型只用别名，从不钉真实 id（我 grep 确认）
`skills/` 全部正文里 `claude-` 开头的真实 model id **0 次命中**，只写 `fable`/`opus`/`sonnet` 三个裸别名（`haiku` 完全缺席）。
先澄清：`fable` 不是造词，是官方 Claude Code 别名——[模型配置 - Claude Code Docs](https://code.claude.com/docs/zh-CN/model-config) 与 [Claude Code model configuration | Claude Help Center](https://support.claude.com/en/articles/11940350-claude-code-model-configuration) 都确认 `claude --model claude-fable-5-1`、且未设 `ANTHROPIC_DEFAULT_FABLE_MODEL` 时别名解析到 Fable 5.1（2026-09-01 发布，1M 上下文，定位长程 agentic coding）。
所以代价不是"用了假模型名"，而是更细的一层：**别名让规格跨版本存活，但能力与价格随宿主解析静默漂移**——宿主把 `fable` 从 5.1 换成 5.5，你生成的整套团队成本与行为同时变了，而规格文件一个字没改。另外逐阶段 `opts.model` 只在 Workflow 模式可用，另两种模式拿不到这个控制粒度。

### 4.8 平台假设（与我这台机器直接相关）
`shasum` 冻结校验、`md5sum`、`_workspace/` 路径约定都假设 POSIX。Windows 上默认没有 `shasum`。如果你要复用它的产物冻结思路，在 TraceCoder 里对应的现成机制是 `content_fingerprint`/`stable_json_hash`（`skills/models.py`、`context/identity.py`），纯 Python 跨平台。

---

## 5. 层位对照：同一个本能，两种强制方式

| 工程本能 | harness 的表达（规格） | TraceCoder 的表达（实现） | 谁更强 |
|---|---|---|---|
| 确定性控制流 | Workflow JS 脚本 `pipeline()`/`parallel()`，禁 `Date.now()` | `agent/loop.py:1032` while 循环 + `loop_limits.py:19` 200/400/3600 + 显式 stop reason | 各半：它的编排表达力比你强（多阶段、fan-out、schema 校验），你的保证力比它强（可断言、宿主不依赖） |
| 可重放 | `resumeFromRunId`，未变 `agent()` 命中缓存（**依赖宿主实现 memo**） | JSONL append-only（`context/store.py:28`）+ replay 重建 `SessionView` | **你**：重放的正确性自己拥有 |
| 中间产物审计 | `_workspace/{phase}_{agent}_{artifact}.ext` + `shasum` 冻结 | `context/writer.py` 14 类事件 + `attachments/` 暂存 | 各半：它的命名可读性好，你的不可篡改 |
| 工具白名单 | agent frontmatter `tools:`，且承认宿主会漏注入 | `SubagentProfile.allowed_tool_names`（`agent/subagent.py:60`）4 角色硬编码 + 子会话独立 PermissionManager | **你**：代码强制、有测试 |
| 失败即信息 | `.filter(Boolean)` + `log()` 记缺失 | 所有失败落成 `ToolResult`，loop 永不崩 | 同信念，你有实现 |
| 不许猜测填坑 | "只填自己核实过的事实" | `execution_evidence.py` + completion gate（`loop.py:1098`） | **你**：它能写成检查项，它写不成执行 |
| 权限边界 | **无**。只有"用 Workflow 需用户同意"的措辞 | `PermissionMode`×`Decision`×`grant` + 写前 diff 重验（`tools/review.py:85`、`permissions/manager.py:44`） | **你**，量级差 |
| 上下文预算 | "Progressive Disclosure"写法 + `budget.remaining()` 脚本约定 | L1/L2/L3 程序压缩 + L4 模型 handoff + `token_budget.py:52` 0.95/0.90/0.72 | **你** |
| skill 触发质量 | **10 该触发 + 10 near-miss 不该触发**，`Train 60/Test 40`、最多 5 轮、按 Test 集选优防过拟合 | `triggers` 只被解析进系统提示目录（`skills/discovery.py:104`→`context/system_prompt.py:34`），**没有任何触发评测集**（`tests/` 里只有列出/详情/加载/replay 的用例） | **它**，这是你的真缺口 |
| 按任务分档模型 | fable/opus/sonnet 三档 + 四条禁止批量钉的规则 + 默认 sonnet | `SubagentProfile` 字段只有 `role/description/allowed_tool_names/allow_background/requires_worktree`，**没有 `model`**；子代理一律跟父模型 | **它**，这是你的真缺口 |
| 有效性验证 | 与/无 skill 的 A/B 对照 | Harbor 任务级 verifier（`benchmark/harbor/`）——只测"能不能过"，不测"加了它是否更好" | **它**补你的短板 |
| 工程质量 | 0 测试 0 CI | 111 测试文件 / 1,299 用例（2026-10-28 实测 1368 passed / 3 failed，失败 2 个是 Windows symlink 权限、1 个是 TUI 30×6 布局） | **你** |
| 交付形态 | `/plugin install`，跑在别人的 runtime 上 | `pipx install tracecoder`，自己就是 runtime | 定位不同 |

一句话：**它的六种队形是你没有的"编排词汇表"；它的三档模型和触发评测是你没有的两个具体特性；你的权限、上下文、可重放、测试和 benchmark 它一样都没有——因为它不需要，那些是它脚下的宿主提供的。**

---

## 6. 拿走三件，别学两件

**拿走（按成本从低到高）**

1. **触发评测**：给现有 skill 造 10 条该触发 + 10 条 near-miss（near-miss 从相邻领域造，不是明显无关的那种），先做**人工一致率**，一致率站得住再谈机器判分——这条守你自己定的指标纪律。落点：新增 fixture + 一个假 provider 的用例。
2. **角色级模型档位**：给 `SubagentProfile` 加 `model`，把它的四条规则变成代码约束（按任务选、禁止批量钉同一档、编排层高档不代表 worker 高档、难判断默认低档），理由写进 profile 数据而不是注释。先确认 delegate 是否直接复用父 provider 实例（我读到 `_child_prompt`/`_create_isolated_child_session` 但未确认模型解析路径）。
3. **对照断言式 QA**：把"存在性检查"改写成"两侧结构是否一致"的检查项（响应字段 vs 消费端类型、路径 vs 路由表、声明状态机 vs 实际写入）——这类检查在你项目里是可 grep、可落 pytest 的，比 harness 只能靠 prompt 提醒强一个量级。

**别学**

1. 把行为写进 markdown 然后指望模型自觉。它自己 v1→v2 就是代价，而且失败是**静默退化**，不是报错。
2. 把"作者自测 n=15、15/15 全胜"当采用依据。它自己标了诚实免责声明，但数字仍然不能支撑"引入这套配置有 60% 收益"这种结论。

---

## 7. 事实核查记录（本文每条判断的来源与置信度）

**我一手核对（高）**：GitHub API 元数据（star/fork/issue/时间/语言/license/v1 仓库存在性）；全树 37 条目与逐文件大小；无 `.github/workflows`（contents API 404）；plugin.json 字段清单；README 全文含第 121 行自测数字逐字；SKILL.md 全文（395 行，含 Phase 0-7、错误处理 5 条、规模表、16 项清单）；`fable` 是官方别名（官方文档检索命中）；grep 确认 `.filter(Boolean)`/`Date.now()`/`resumeFromRunId`/`budget.remaining`/`1,000`/`judgeCount`/`dryRuns`/`shasum`/`50_000` 存在、`haiku` 与 `claude-` 真实 id 在 skills 正文中 0 命中；TraceCoder 侧全部行号（`loop.py:1032/1098`、`loop_limits.py:19`、`subagent.py:60`、`review.py:85`、`store.py:28`、`token_budget.py:52`、`discovery.py:104`、`system_prompt.py:34`）来自我自己 Read/Grep；测试数 1,299 与 1368/3/3 来自我实跑。

**子代理全文读取、我未逐条复核（中~中高）**：execution-modes / team-patterns / orchestrator-template / workflow-recipes / qa-agent-guide / model-selection / skill-writing / skill-testing / team-examples / evolve SKILL.md / migration 文档的具体引文与数值（含"静默退化为单 agent 执行"、Train 60/Test 40、10+10 vs 3+3 矛盾、timing.json 手抄、五例无真实产物文件、`tools` 未注入的 2.1.226 观察）。

**待复核（低，别当结论用）**：`/harness:evolve` 命令在真实 Claude Code 里到底能否通过目录约定生效；CHANGELOG 150 行的完整版本史我只抽查了关键词，没通读。

**源文件本地位置**：`C:\Users\EDY\AppData\Local\Temp\harness-src\`（临时目录，重启可能丢；要长期保留就复制到本目录旁）。

---

## 8. 关联

- 你项目侧的读法：[TraceCoder最小源码导读](TraceCoder最小源码导读.md)
- 上层规格 ↔ 下层实现的接口：`tracecoder/skills/discovery.py:112` 已在扫 `*/SKILL.md` + frontmatter，`SkillSource.PROJECT_AGENT_SKILL` 这个枚举就是给 Anthropic 生态格式留的位置——**harness 生成的 skill 理论上能被你的 loader 装载**（还没写兼容性测试，见 §6 第 1 条）。
