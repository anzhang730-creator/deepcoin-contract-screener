# Deepcoin 合约交易对筛选器

这是一个使用 Deepcoin 公共行情接口的本地筛选器，当前范围是线性永续合约。用户可以手动组合条件，应用会分别返回已收盘 K 线结果和实时价格结果。

## Windows 一键运行

解压 `DeepcoinScreen-Windows.zip`，双击 `start_deepcoin.bat`。它会启动本地服务并自动打开浏览器地址 `http://127.0.0.1:8000`。发布包内置运行环境，不需要安装 Python；使用时需要联网访问 Deepcoin 公共行情接口。

打包版启动后会检查 GitHub 最新版本，并在运行期间每 15 分钟检查一次。发现新版后会自动下载 SHA-256 校验清单和程序包，校验通过后关闭旧程序、备份并替换文件、启动新版；新版未通过本机健康检查时会恢复旧文件并重启。更新记录保存在 `%LOCALAPPDATA%\DeepcoinScreen\update.log`。每台电脑首次仍需部署一个包含自动更新器的发布包，此后的版本可以由程序自行更新。

## 源码运行

需要 Python 3.10 或更高版本，不需要安装第三方包：

```powershell
python app.py
```

浏览器打开 <http://127.0.0.1:8000>。

## 当前支持

- Deepcoin USDT / USDC 线性永续合约
- 1 分钟、5 分钟、15 分钟、30 分钟、1 小时、4 小时、12 小时、1 日、1 周 K 线
- 最新价、24 小时涨跌幅、24 小时成交额
- MA、EMA、RSI、布林带上轨 / 中轨 / 下轨 / 带宽
- Pine「高质量关键位支撑压力」条件：动态支撑/压力区、触碰/突破/回踩事件、高周期共振，以及前日/前周高低点、当日开盘和会话 VWAP
- 裸 K 形态：支撑锤型线拒绝、压力位长上影拒绝；支持回看 1–100 根已收盘 K 线，并按形态实际触及区域的质量分过滤
- 关键位作为单条条件添加；在同一行选择信号，质量分或区域距离条件再填写阈值
- 大于、大于等于、小于、小于等于、等于、不等于
- 多个条件同时满足
- 每条条件可以单独选择周期，支持多个周期同时满足
- 收盘 K 线 / 实时价格双结果视图
- 点击收盘结果、实时结果或涨跌幅列表中的交易对名称 ↗，在新标签页打开对应 Deepcoin 合约页面
- 启动后后台每 60 秒交叉核对公开合约交易状态、官网路由清单、官网交易清单；新增、下架、重新上线随下一轮成功核验自动更新，不按固定名称或资产类型硬编码排除
- 官网两份清单不一致、官网先上线而行情接口未同步时，记录为“待核验”，每 15 秒重试，不判定为下架；临时故障保留上次核验记录，暂停更新结果及跳转，恢复后自动重新发布
- 顶部“官网合约自检”展示核验时间、已确认及未列出/待核验数量；展开明细可搜索名称、查看原因和最近变更，也可点击“立即核验”
- 已显示结果会随核验状态变化移除失效入口；点击交易对通过本地入口再次检查当前目录，未确认的合约不会跳到 BTCUSDT
- 独立涨跌幅排序：1 分钟、5 分钟、15 分钟、30 分钟、1 小时、4 小时、12 小时、1 日、1 周
- 排序同时展示当前价涨跌幅和收盘涨跌幅，可切换涨幅榜 / 跌幅榜
- 可手动启动完整筛选；自动重筛默认关闭，开启后每 60 秒按当前条件重新筛选，运行中不会重复排队
- K 线按交易对和周期缓存，仅在需要时更新；扫描结果显示 K 线请求失败数与耗时
- Deepcoin 分钟 K 线周期按接口要求使用小写单位，扫描结果会显示失败组数和耗时
- 扫描全部高成交额交易对，或输入指定交易对

行情接口不需要 API Key。API Key 不会提升公开行情的指标计算速度；当前版本通过行情缓存、条件预过滤和并发 K 线请求优化扫描速度。筛选结果仅供研究使用。

关键位公式和默认参数按 `tradingview_key_levels_v2.pine` 移植，筛选数据使用 Deepcoin 合约 OHLCV。TradingView 图表若使用不同交易所的数据源、合约或 K 线边界，公式一致但数值和触发时间仍可能不同；要逐值相同，图表需使用同一 Deepcoin 合约行情和周期。

“已确认”以本机网络可访问的官网公开交易清单与官方 `live` 状态为准，不代表特定账户已经获得地区、认证或下单权限。目录更新采用轮询，并非交易所推送的瞬时上下架事件。官网匿名清单接口属于网页接口，如果返回格式或校验规则变化，程序会报告核验异常，等待适配，不会将错误响应当成空清单批量排除合约。

涨跌幅排序的“周期起点价”是该周期开始前一根已收盘 K 线的收盘价；“当前涨跌幅”使用最新 ticker 价格计算，“收盘涨跌幅”使用最新已收盘 K 线计算。Deepcoin 原生接口不支持 3 分钟、2 小时、6 小时、3 日等周期，因此界面不会显示这些会直接报错的选项。
## 多屏图表轮播

## Update workflow

The packaged Windows application includes automatic update support. On the main page, click `检查更新` to check the configured GitHub repository. When a newer release is found, the packaged application downloads and verifies the release, closes the old process, replaces it, and starts again. The background check runs periodically as well.

For another computer, install the complete `DeepcoinScreen-Windows.zip` once and run `start_deepcoin.bat`. Later updates work only after a matching GitHub Release tag and assets are published: upload `DeepcoinScreen-Windows.zip` and `SHA256SUMS.txt` to the release whose tag matches `version.txt` (for example `v1.0.5`). Do not distribute only the executable, because the updater also relies on `version.txt` and the bundled runtime.

完成一次筛选后，在结果页底部的“筛选结果自动轮播”中选择显示器、停留秒数和收盘/实时模式，点击“启动轮播”。程序会用独立的 Edge 或 Chrome 窗口打开当前筛选结果，并将窗口移动到选定显示器；轮播窗口提供暂停、下一个和关闭按钮。目标电脑需要安装 Microsoft Edge 或 Google Chrome，显示器列表由 Windows 当前工作区实时读取。
