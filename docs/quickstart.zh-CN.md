# 中文快速上手

本服务提供标准 MCP 购物领域工具，宿主负责推理，不需要额外模型 key。当前正式平台仅允许公开只读 GET；真实购物账户、购物车和下单功能均未接入。完整订单验收只在明确标记为 SYNTHETIC 的回环店面运行。

按照英文 README 安装依赖并执行两组测试。运行时 SQLite、运维配置、证据必须放在所有 Git checkout 之外，目录权限 0700，文件 0600；拒绝符号链接和不安全权限。不要提供真实地址、支付信息或浏览器 profile，只使用 opaque 引用。

运维人员先创建共享预算和稳定采购目标，取得 task_id，再把 stdio 启动命令配置给宿主。MCP 不提供创建采购目标、调整预算或批准订单的工具。同一目标不能通过换链接、卖家、商品标签、规格、平台或重建报价绕过提交锁。多个宿主必须显式使用相同 namespace，并被目标 ACL 授权；知道 task_id 不代表有权限。

调用顺序：shopping_capabilities → shopping_search → shopping_inspect → shopping_compare → shopping_preview_purchase。compare 必须明确确认同 SKU、规格与成色，未知运税费不按零计算，积分不冲抵应付。FX 必须由运维控制面登记来源、时间、有效期、换汇费率及保留金额。不同币种的购买预算当前直接阻断。

预览不产生订单。开发测试中的 nanami-operator grant-synthetic 只签发合成授权，不代表已集成真实主人批准。不要将运维 CLI、状态数据库或配置写权限暴露给模型。实际 commit 对五个平台始终关闭。

shopping_get_task 可跨进程接续并分页读取候选。shopping_get_order 只能读取或用持久商家收据对账，不会再次提交。submitting/uncertain 始终保留目标锁和预算占额；cancel_task 不承诺撤销商家已接受的订单。

显式只读联网 smoke：`python scripts/live_smoke.py --allow-public-network`。它不属于默认测试或 CI，不登录、不保存 cookie、不绕过验证码。乐天和 Yahoo 支持公开商品链接与部分结构化字段；Amazon、Mercari、PDD 不宣称候选提取已就绪。PDD 日本包邮必须验证具体规格及结算证据，当前尚未实现。

HTTP、多用户隔离部署及真实主人授权集成均为后续工作。本版本的权限边界是受信任本地运维人员控制的进程配置；不能抵抗拥有相同操作系统用户权限并能直接修改 SQLite 的恶意宿主。
