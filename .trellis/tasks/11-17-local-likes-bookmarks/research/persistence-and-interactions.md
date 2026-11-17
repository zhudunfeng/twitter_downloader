# Research: 本地喜欢、多收藏夹的持久化与交互一致性

- Query: 为下载推文增加本地喜欢、自定义多收藏夹与独立列表，比较存储方案，明确身份、异步写入、分页和恢复边界。
- Scope: mixed；只研究现有 Python/PySide6/SQLite 工程与官方资料，不实施产品代码。
- Date: 2026-11-17

## Findings

### 1. 现有代码与约束

| 文件 | 已验证的模式及修改关联 |
| --- | --- |
| `twitter_downloader/library/repository.py:149-248` | 线程所属连接；catalog schema 2；WAL/NORMAL；CORRUPT/NOTADB 时备份整个库与 sidecar 并重建。个人状态不能沿用这项可丢弃策略。 |
| `twitter_downloader/library/repository.py:285-348,386-455` | upsert 只管作品；SQL 筛选后分页；坏 payload 会删除；完整扫描 prune 删除消失目录的索引行。 |
| `twitter_downloader/library/reader.py:249,339-344` | `tweet_id` 会回退目录名；`key` 是规范化 root + 相对作品目录的 SHA-256。 |
| `twitter_downloader/library/models.py:21-53` | `LibraryWork` 不可变；`like_count` 是下载时公开计数；`LibraryQuery` 已有搜索、作者、类型、日期、排序。 |
| `twitter_downloader/gui/library_page.py:28-98,379-455` | 作业在 `run()` 创建/关闭连接；筛选 generation 丢弃旧结果；`_reload()` 最终重置全部已加载模型。 |
| `twitter_downloader/gui/timeline_model.py:9-83` | 内存实体、WORK_ROLE 与 key→row；已有 reset/append，尚无局部状态更新/移除方法。 |
| `twitter_downloader/gui/timeline_view.py:80-147,301-319` | reset 会清布局缓存；已有像素锚点与正文展开状态；不能每点一次喜欢就全量 reload。 |
| `twitter_downloader/gui/tweet_delegate.py:495-532`；`gui/config.py:11-20` | 现有底部“喜欢”只是公开计数快照；用户目录已有 `library.sqlite3`，适合同级增加个人库。 |

相关规格：`.trellis/spec/backend/local-library.md:5-10,67-96,104-109,123-145` 明确原件权威、catalog 可重建、每连接归所属 worker、paint/model read 无 SQL/IO、唯一播放器与阅读锚点。`database-guidelines.md` 仍是模板，不能用来覆盖上述实际契约。根目录 AGENTS.md 还要求中文方法/字段/关键逻辑注释、类型、中文业务日志；本轮不改依赖、不建测试类。

### 2. 三种存储方案

| 方案 | 刷新/重扫 | prune、删除后重新导入 | catalog 重建/损坏恢复 | 结论 |
| --- | --- | --- | --- | --- |
| `library_works` 新增个人列 | 必须改 upsert，防止每次覆盖；多收藏夹仍需关系表 | 索引行删除即丢标记 | 整库恢复后无原件可重建个人数据 | 看似最少修改，实际破坏缓存契约，不推荐。 |
| 同一 catalog 的独立个人表 | 独立写表可避免普通 upsert 覆盖 | 不外键级联 catalog 可保留孤儿记录 | catalog 整库重建仍使个人表丢失；必须重做现有恢复策略 | 可行，但扩大缓存生命周期改造，不是最小可靠方案。 |
| 同级独立 `personal.sqlite3` | 扫描只写 catalog，个人数据独立 | 保留个人孤儿记录，同 key 重导入恢复；从列表暂时消失不等于取消标记 | catalog 重建无需动个人库；个人库单独备份/报错/恢复 | **推荐**：明确分开可重建索引与不可重建个人记录。 |

个人库是用户数据，不能放进预览缓存，也不能写回 `tweet.json`、Markdown、媒体或下载器计数字段。catalog 仍为 schema 2；个人库自己维护 `user_version=1` 和迁移。CLI、扫描、原始 `LibraryWork` payload 不夹带个人状态；新增不可变个人状态 DTO/查询结果，避免 `asdict(work)` 把个人字段重复写进缓存。

### 3. 推荐身份与个人 schema

首版沿用 **`work.key`，标记属于本地副本**：同路径反复扫描、重启、catalog 重建以及删除后在同一位置重导入可恢复；不同 root 的副本互不影响。移动根目录、重命名作品目录或把重叠目录以另一个 root 导入会产生新 key，**不自动继承**，不做猜测式搬迁匹配。

未来若改为 `x:status:<tweet_id>`，须在 reader 中显式区分经过验证的元数据 ID、规范 X/Twitter status URL 的一致 ID，以及目录回退值，并处理冲突。仅“字符串是数字”不足以把目录名变为可信平台身份；当前 `LibraryWork.tweet_id` 不携带来源，不可直接作为全局个人主键。全局 ID 方案还会使副本同步，必须先定代表副本选择、合并状态、去重 count/pagination 和离线副本规则；这些不属于本轮最小方案。

建议个人表（时间统一 UTC 毫秒整数）：

- `work_state(work_key TEXT PRIMARY KEY, liked_at INTEGER NULL, collected_at INTEGER NULL, revision INTEGER NOT NULL DEFAULT 0)`：liked_at 非空即本地喜欢；collected_at 仅为当前连续收藏周期的首次时间；revision 仅实际状态变更递增，不能把整个旧对象回写覆盖另一动作。
- `folders(id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL, is_system INTEGER NOT NULL, created_at INTEGER NOT NULL)`：稳定 ID 与展示名分开；名称去首尾空白并以统一规范生成 name_key，不能依靠 SQLite NOCASE 处理全部 Unicode 重名。
- `folder_items(folder_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE, work_key TEXT NOT NULL, added_at INTEGER NOT NULL, PRIMARY KEY(folder_id,work_key))`：一作品可在多夹；重复加入不更新时间；增加 `(work_key, added_at, folder_id)` 索引供 EXISTS/批量水合。
- 不设置到 catalog 的外键，不按扫描结果删除个人行；每个个人写连接显式 `PRAGMA foreign_keys=ON`。[S6]
- 系统默认夹固定 ID，首次事务中幂等建立，不能删除。收藏状态永远由任意 membership 的 EXISTS 得出，**不另存 bookmark 布尔值**。

收藏主导航内使用“全部收藏/指定收藏夹”选择器和管理按钮，不将任意数量收藏夹平铺到侧栏。点击收藏图标打开多选选择器：新收藏预勾默认夹，已有收藏勾现有归属，**明确保存才提交**，取消/关闭不写库；选择器可创建/重命名自定义夹。明确区分：①移出当前夹，只删当前 membership；删除最后一个归属即取消收藏；②取消全部收藏，删除该作品全部 membership，喜欢不变。删除自定义夹不删作品：同事务把“只属于此夹”的作品转入默认夹并保留 added_at，再删夹；多归属作品保留其他归属。此策略避免删夹隐式批量取消收藏。选择器清空全部时应明确提示将取消收藏，而非偷偷加回默认夹。首版平级、无嵌套。

推荐顺序：喜欢按 liked_at DESC、全部收藏按 collected_at DESC、指定夹按 membership.added_at DESC，均以 work.key ASC 确定并列顺序。每次保存归属在一个个人写事务里比较旧/新 membership 是否为空：0→1 设 collected_at；1→1 保留；1→0 清空；重新收藏开始新周期。给第二个夹加入、从最早夹移出、删夹后转默认均不改变连续周期。重复加入保留原 added_at。**是否收藏始终用 EXISTS，不用 collected_at 非空代替**；这项时间元数据和 membership 的一致性必须由单一事务入口维护，不能做两次独立提交。相比 MIN(added_at)，增加一个 nullable 列即可满足“首次时间不随换夹变化”，没有增加第四张表。

### 4. ATTACH 查询、计数与分页

推荐 catalog 连接在其 worker 中完成既有初始化后，附加个人库为 `personal`；路径绑定参数、schema 名固定。个人库先由独立仓储初始化/验证；查询可用受控 file URI `mode=ro` 附加，Python 连接启用 URI 支持，防止路径拼错时创建一份新个人库。[S2,S5] **ATTACH/个人校验不可放进现有 catalog `_open/_initialize` 的损坏兜底范围**，否则个人库损坏会被误判成 catalog 损坏。个人异常须包装并单独处理。

查询在 SQL 中先筛选，再 ORDER/LIMIT/OFFSET，禁止取出 80 条后 Python 过滤：

```sql
-- 全部收藏：同作品在两个收藏夹仍只输出一条 catalog 记录。
SELECT w.key, w.payload
FROM library_works AS w
LEFT JOIN personal.work_state AS ws ON ws.work_key=w.key
WHERE EXISTS (
  SELECT 1 FROM personal.folder_items AS fi WHERE fi.work_key=w.key
)
ORDER BY ws.collected_at DESC, w.key ASC LIMIT ? OFFSET ?;
```

喜欢用 `EXISTS(personal.work_state WHERE work_key=w.key AND liked_at IS NOT NULL)`；指定收藏夹给同一 membership EXISTS 加 `fi.folder_id=?`；全部作品不加个人条件。`LibraryQuery` 可新增固定枚举 scope 与 folder_id，并与搜索/作者/日期/媒体筛选做 AND；主查询、总数、导航徽标共用条件构造器。按时间排序可 LEFT JOIN 一对一 work_state，或为指定夹限定唯一 `(folder_id,work_key)`；不要直接 JOIN 任意 memberships 后 COUNT(*)/LIMIT，它会按归属数重复作品。加载页后批量读取该页归属，禁止每行 N+1；所有 hydration 在 worker 中结束，GUI 收到完整 DTO。

需要一致 page/count/个人状态快照时，在同一 worker 连接上显式开启短读事务；Python `with connection` 本身不会为 SELECT 自动开启事务。[S1,S5] 现有 `query()` 的坏 payload 清理可能提交事务，要把这类 catalog 修复延后或重查，不能破坏查询边界。不能承诺跨库任意时刻的全局快照，也不需要跨库同时写入原子性：扫描只写 main；个人动作只写 personal 的独立连接。SQLite 在 WAL 下不保证多附加库联合提交原子性，故不设计依赖它的协议。[S2]

本机 SQLite 3.50.4 的两次纯内存验证已通过：两夹共同包含 a、另一夹包含 b 时 EXISTS COUNT=2、分页不重复；三表方案下 1→1 保留 collected_at、1→0 清空、重新 0→1 使用新时间；重复喜欢保留 liked_at、取消喜欢不影响归属；删除自定义夹后转默认，保留 added_at/collected_at。该验证仅证明 SQL/幂等语义，不代替真实文件锁、Qt 生命周期和损坏恢复验证。

### 5. 异步写入与界面一致性

- 独立有界个人写队列，连接只在所属 worker 创建/使用/关闭；短事务、有限 BUSY/LOCKED 等待。不要每次点击创建无界 QThread，也不要把扫描长作业当作个人写队列。事务提交后才发成功事件；失败 rollback 并保留上次确认状态。[S1]
- 按 `(work_key, action)` 保存 GUI 的 confirmed、desired 与单调 revision；DB 命令发送绝对 set(true/false)、目标 folder 集合或明确增删归属，**不发 SQL toggle**。同一作品串行化；相同动作仅合并尚未发出的最新 desired，喜欢与收藏互不覆盖。folder 删除/改名与 membership 变更也经同一序列，并在事务内重验 folder 存在性。
- 可立即乐观更新图标并显示保存中；当前 revision 失败才回退。旧成功仍更新其已确认基线，但不能覆盖新 desired；旧失败不能把后续点击撤回。失败反馈“喜欢保存失败/收藏夹修改失败”，记录中文日志，给重试入口。导航 generation 与写 revision 分开；离开列表不能丢弃已经成功的个人状态。
- 查询结果还带个人 state epoch；过期查询不能复活已取消的条目。队列确认后递增 epoch，失效旧分页请求；重查当前已加载区间以校正 offset/count，最终用 key 差分合并，避免删除一行后下一页漏项。待提交 desired 覆盖所有查询水合结果，最终在写队列稳定后再做权威查询。
- 模型增加个人角色/按 key 更新：普通图标更新只发 `dataChanged`（含相关角色），不调用 `beginResetModel`。从喜欢/单夹/全部收藏移除不再匹配的作品，用 `beginRemoveRows/endRemoveRows` 配对；失败可用相反插入恢复。只能 GUI 线程修改模型，worker 用 queued signal 返回普通值。[S3]
- **取消当前筛选中的最后一个标记**：保留当前 scope，移除行并更新总数/徽标；没有其他匹配项时显示该列表空态；若只是本页删空但还有后页则补页。锚点优先原顶部 key，不存在则选后继、再前驱，禁止直接跳到全库顶部。移出当前夹但仍在其他夹时，全部收藏仍显示。
- 点标记不关闭详情/大图，不重建播放器、不重载媒体、不清预览缓存、不重置展开状态。行仍在时保留选择、锚点、视频 position/rate/mute；若筛选导致正在播放的行消失，按既有 out-of-view 契约暂停并保留播放位置，不自动播放邻项。多个详情窗口按 key 同步按钮。导航切换仍沿用同一个 LibraryPage 与唯一播放器。[S3，项目 local-library 契约]
- 退出时停止接受新动作，排空已接受的有界写队列并等待连接关闭；不可像可取消查询一样静默丢弃排队个人操作。无法保存则明确提示未保存内容，不能报告完成。

### 6. 个人库恢复与版本风险

普通重扫、catalog 损坏重建、root 暂时离线均不动个人库。个人库 BUSY/LOCKED、权限/磁盘满不是损坏；有限重试后保留旧库与旧确认状态。个人库 CORRUPT/NOTADB 或未来 schema 应停止个人写入、保留原库/sidecar、显示中文错误并允许恢复；**不能套用 catalog 自动清空重建**。主作品列表仍可阅读，喜欢/收藏夹筛选应说明不可用，不能把读取失败表现为“没有收藏”。在线备份使用 sqlite3 backup API，不能把运行中 DB 文件单独复制当作一致备份。[S4,S5]

已实测运行时：Python 3.14.4、SQLite 3.50.4、PySide6 6.11.1。本次不修改依赖。建议个人库使用 rollback `journal_mode=DELETE`，每个个人写连接设置 `synchronous=EXTRA`，与串行短写配合，避免照搬 catalog 的 WAL/NORMAL 持久性与并发假设；仅初始化/迁移时执行 DDL。SQLite 官方还披露 WAL-reset bug：3.7.0 至 3.51.2 受影响；3.51.3（2026-03-13）修复，3.50.7 有回补。3.50.4 在受影响范围，但不代表当前一定触发；既有 catalog 的 WAL 是需记录的现存缓存风险，不能用本次功能计划擅自升级系统。[S7,S8]

### 7. 官方资料与版本依据

- [S1 SQLite Transactions](https://www.sqlite.org/lang_transaction.html)：读写事务、单写者、BEGIN IMMEDIATE、BUSY 与显式回滚；访问 2026-11-17。
- [S2 SQLite ATTACH DATABASE](https://www.sqlite.org/lang_attach.html)：跨附加库查询、限定表名、URI 与 WAL 下联合提交限制；访问 2026-11-17。
- [S3 Qt for Python / QAbstractItemModel](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QAbstractItemModel.html)：GUI 线程模型所有权、queued 更新、dataChanged、行增删/重置通知；Qt 6 在线资料，本机 PySide6 6.11.1。
- [S4 SQLite Online Backup API](https://www.sqlite.org/backup.html)：在线备份与普通文件复制的区别；访问 2026-11-17。
- [S5 Python 3.14 sqlite3](https://docs.python.org/3.14/library/sqlite3.html)：URI mode=ro、显式事务/上下文管理器、backup；本机 Python 3.14.4。
- [S6 SQLite Foreign Key Support](https://www.sqlite.org/foreignkeys.html)：外键约束须在每个连接开启；访问 2026-11-17。
- [S7 SQLite WAL](https://www.sqlite.org/wal.html)：官方 §8 WAL-reset bug、受影响/修复版本与触发条件；访问 2026-11-17。
- [S8 SQLite PRAGMA synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous)：NORMAL/FULL/EXTRA 与 rollback/WAL 模式的持久性语义；访问 2026-11-17。

## Caveats / Not Found

- 研究开始时 `.trellis/.current-task` 仍指向 `11-16-local-library-viewer`；按委派明确指定，将本研究写到 `11-17-local-likes-bookmarks`。主任务随后已将 current-task 切换到本次计划；本研究未修改其他任务文件。
- 本地副本身份不防止用户把完全不同内容覆盖到同一路径：无可信平台身份时无法可靠辨别“原作品重导入”与“目录复用”。首版明确此限制，不用正文 hash 猜迁移（原件补全会改变正文/签名）。
- 个人库保留已删除作品的孤儿标记；只有 catalog 存在对应 key 时列表可见，孤儿不会计入可阅读作品总数。没有自动删除个人孤儿、跨机同步或自动搬家承诺。
- 本轮没有进行真实 DB 故障注入、并发进程/断电、Qt 输入与媒体回归；这些属于实施后的验收。未新增源码、依赖或 unittest 类。
