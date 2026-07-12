# CLAUDE.md — trade-sync

## 專案目的

每個交易日收盤後（16:00 台灣時間），自動從各券商 API 抓取當日成交明細，
合併同一訂單的多筆部分成交後，append 寫入 Google Sheets 對帳表。

## 架構概覽

```
trade-sync/
├── main.py              # 執行入口（--date / --backfill-pnl / --import-statement）
├── config.py            # 環境變數、broker factory
├── models.py            # Trade / Position / PortfolioSnapshot dataclass + merge_fills()
├── sheets_client.py     # Google Sheets 讀寫（不覆蓋、backfill、每日部位快照）
├── statement_ctbc.py    # 中信月對帳單 PDF 解析（無 API，改匯入 PDF）
├── stock_names.py       # 股票代號→名稱對照（從 Sheet tab 載入）
├── brokers/
│   ├── base.py          # BrokerClient 抽象介面
│   ├── fugle.py         # 元富/台新（taishin_sdk）
│   ├── esun.py          # 玉山（esun_trade SDK）
│   └── __init__.py
├── requirements.txt
├── .env.example
└── .github/workflows/sync.yml
```

## 資料流

```
main.py
  ├─ SheetsConfig.from_env()
  ├─ get_last_dates(cfg)          ← 讀 Sheet，找各帳戶最後記錄日期
  ├─ get_enabled_brokers()        ← 依 ENABLED_BROKERS 環境變數
  └─ for each broker:
       ├─ skip if target_date <= last_date[account]
       ├─ broker.get_fills(target_date) → List[Trade]
       ├─ merge_fills()           ← 合併部分成交
       └─ append_rows(cfg, rows)  ← 寫入 Sheet
```

## Google Sheets 欄位（共 15 欄）

| 欄 | 名稱       | 格式範例              | 說明                          |
|----|------------|-----------------------|-------------------------------|
| A  | 交易日期   | 2026/01/02            | YYYY/MM/DD                    |
| B  | 交易類型   | 買進/賣出/當沖買入/當沖賣出 |                           |
| C  | 股名       | 6770 力積電           | 代號空格名稱                  |
| D  | 平均成本   | 39.55                 | 持倉成本；買進為 0            |
| E  | 成交價     | 39.80                 | 部分成交時為加權均價          |
| F  | 股數       | 1000                  |                               |
| G  | 成交金額   | 39800                 |                               |
| H  | 手續費     | 57                    | 對帳單實際金額                |
| I  | 交易稅     | 119                   | 賣出才有                      |
| J  | 已實現損益 | 19                    | API 提供時填入，否則留空      |
| K  | 已實現損益率| 0.05%                | Sheet 公式計算，程式留空      |
| L  | 融券融資費 |                       | API 提供時填入，否則留空      |
| M  | 股票帳戶   | 元富                  | 來自 broker.account_name      |
| N  | 策略       |                       | 手動填，程式留空              |
| O  | 筆記       |                       | 手動填，程式留空              |

## 不覆蓋邏輯

- 執行前先讀 Sheet 欄 A（日期）和欄 M（帳戶）
- 找出各帳戶的 `last_date`（最後記錄日期）
- 若 `target_date <= last_date`，該帳戶當日資料直接跳過
- 新資料只 append 到 sheet 末端，不修改既有列

## 新增券商

1. 在 `brokers/` 建立新檔案，繼承 `BrokerClient`，實作 `account_name` 和 `get_fills()`
2. 在 `config.py` 的 `BROKER_REGISTRY` 加入對應 factory function
3. 在 `BROKER_REGISTRY` dict 下方加入環境變數讀取邏輯
4. 更新 `.env.example` 和 `.github/workflows/sync.yml`

## 環境變數

| 變數                          | 用途                                      |
|-------------------------------|-------------------------------------------|
| `ENABLED_BROKERS`             | 逗號分隔，e.g. `fugle` 或 `fugle,esun`   |
| `FUGLE_IDENTITY`              | 元富身分證字號                            |
| `FUGLE_PASSWD`                | 元富登入密碼                              |
| `FUGLE_CERT_PATH`             | 憑證路徑，e.g. `./certs/fugle.pfx`       |
| `FUGLE_CERT_PASS`             | 憑證密碼                                  |
| `FUGLE_ACCOUNT_NAME`          | Sheet 顯示名稱（預設：元富）              |
| `ESUN_API_KEY`                | 玉山 API key                              |
| `ESUN_API_SECRET`             | 玉山 API secret                           |
| `ESUN_ACCOUNT`                | 玉山帳號                                  |
| `ESUN_PASSWD`                 | 玉山密碼                                  |
| `ESUN_CERT_PATH`              | 憑證路徑，e.g. `./certs/esun.p12`        |
| `ESUN_CERT_PASS`              | 憑證密碼                                  |
| `ESUN_ACCOUNT_NAME`           | Sheet 顯示名稱（預設：玉山）              |
| `CTBC_STATEMENT_PASSWORD`     | 中信對帳單 PDF 密碼（身分證字號）         |
| `CTBC_ACCOUNT_NAME`           | Sheet 顯示名稱（預設：中信）              |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service Account JSON 字串                |
| `GOOGLE_SHEET_ID`             | 試算表 ID                                 |
| `GOOGLE_SHEET_NAME`           | 工作表名稱（預設：對帳單）                |
| `GOOGLE_STOCK_INFO_TAB`       | 股票代號 tab 名稱（預設：股票代號）       |

## 部署（本機 launchd 排程）

元富/玉山 SDK 為 macOS-only wheel（`macosx_arm64`），無法在 Linux CI 執行，
故改用 macOS **launchd** 在本機排程。共兩個 LaunchAgent（`~/Library/LaunchAgents/`）：

| 用途 | Label | 時間 | log |
|------|-------|------|-----|
| 主同步（抓當日成交，損益留空） | `com.minjyun.trade-sync` | 週一~五 16:05 | `log/cron-sync.log` |
| 隔日補損益（`run_backfill.sh` → `--backfill-pnl 前一天`） | `com.minjyun.trade-sync-backfill` | 週二~六 00:10 | `log/cron-backfill.log` |

- backfill 排週二~六、補「前一天」（週一~五）：券商已實現損益過午夜才結算，隔天 00:10 才補得到
- `run_backfill.sh` 用 `date -v-1d` 算前一天日期，並含 5 分鐘 watchdog（SDK 無 timeout，防卡死的 job 佔住 launchd 擋掉之後排程）
- 手動觸發：`launchctl kickstart -k gui/$(id -u)/<Label>`
- 中信對帳單為每月手動 `--import-statement`（PDF 由 email 寄送，無法排程）

`.github/workflows/sync.yml` 已停用（需 macOS runner 才能跑，保留供參考）。

### 已知問題：IPv6 導致 Google 連線卡死

本機對外的 IPv6 路由不通，而 `google-auth` 更新 OAuth token 時未設 timeout，
Python 會卡死在 IPv6 socket 連線上（症狀：`stock_names.load` / 任何 Sheet 讀寫
無限等待；curl 因 happy-eyeballs 退回 IPv4 不受影響）。`main.py` 開頭設
`urllib3.util.connection.HAS_IPV6 = False` 強制走 IPv4 解決。動到 Google 連線
若又出現無故卡住，先查是不是這條。

## 執行

```bash
cp .env.example .env   # 填入真實憑證
pip install -r requirements.txt
python main.py                             # 今日
python main.py --date 2026-03-15           # 指定日期
python main.py --backfill-pnl 2026-04-17  # 補填指定日期的損益與平均成本
python main.py --import-statement 對帳單.pdf  # 匯入中信月對帳單 PDF
```

### --import-statement 說明（中信）

中信無對外 API（下單/查詢需其 Windows 專用程式），改用每月寄送的
「綜合月對帳單」PDF 匯入。`statement_ctbc.py` 解析「國內有價證券交易明細」
表格，每筆成交轉成一列 append 到 Sheet。

- PDF 為加密檔，密碼為身分證字號，取自 `CTBC_STATEMENT_PASSWORD` 或 `--password`
- 金額/手續費/交易稅**直接採用對帳單數字**（結算後最終值，最準確）
- 表格買進列無「交易稅」欄、賣出列才有，依交易別的「買/賣」判斷欄位數
- 沿用不覆蓋邏輯：跳過中信帳戶 `last_date`(含)以前的交易，只 append 新交易
- **不做 merge**：對帳單本身已是結算明細，1 列對 1 筆，保留原始成交價
- **限制**：對帳單無成本基礎，故賣出的 D（平均成本）留空、J（已實現損益）留空，
  需手動或 Sheet 公式補；當沖無法從對帳單辨識，一律標「買進/賣出」（稅額仍正確）

### --backfill-pnl 說明

當天 `realized_profit_and_loses` 尚未結算（批次處理，隔日才有資料），
成交當下寫入 Sheet 時 D（平均成本）和 J（已實現損益）會留空。
隔日執行此指令可補填：

- 掃 Sheet 找符合：日期 == 指定日期 + 帳戶 == 券商帳戶名稱 + 類型 in {賣出,當沖賣出} + J 欄為空
- 呼叫 `sdk.accounting.realized_profit_and_loses` 取得損益
- 按股數比例分配 PnL，`batch_update` 寫回 D 和 J 欄

## 每日部位快照（已實測確認）

主同步（16:05）尾端順跑：對每個支援的 broker 呼叫 `get_portfolio()`，寫入兩個
**分年度、自動建立**的工作表。中信無此 API，用 `hasattr` 跳過。

- 只在 `target_date == 今天`才快照（庫存 API 只回當下部位），**沒成交也記一筆**
- 工作表名含年份、跨年自動開新的；**每日總覽每帳戶一張 tab**（方便各自畫資產曲線）
- 不覆蓋：同日已存在就跳過（每帳戶一張，掃該 tab 的日期欄即可）

**「每日持股 YYYY」**（單一 tab，逐檔）：日期, 帳戶, 股名, 股數, 平均成本, 市價, 市值, 未實現損益, 未實現損益率
**「每日總覽-<帳戶> YYYY」**（每帳戶一張）：日期, 持股市值, 現金, 未交割淨額, 帳戶總值, 未實現損益, 入金出金, 累計淨注資, 真實累計損益

- A–G 由程式寫值；H `累計淨注資`、I `真實累計損益` 由程式**逐列寫公式**（append 時用 `col_values` 算列號、`update` 寫 A–I，不用 append_rows 以免公式欄空值干擾）
- `入金出金`（G）程式留空、**手動填**（入 +、出 −）；股利/折讓是真收益、自動進帳戶總值，不用標記，只有外部注資要記
- `累計淨注資`（H）= `=SUM($G$2:G{r})`；`真實累計損益`（I）= `=E{r}-$E$2-H{r}`
- **baseline = 該 tab 第一列（$E$2）的帳戶總值**：真實累計損益從追蹤起點算 0，之後衡量「相對起點的變化 − 期間注資」；跨年新 tab 自動用該年第一列當起點
- 兩張圖（overlay）：`帳戶總值 vs 日期`、`真實累計損益 vs 日期`（一次性 addChart，非程式維護）

### 帳戶總值公式（重點：要算 T+2 交割款）

> **帳戶總值 = 持股市值 + 現金 + 未交割淨額**（應收 +、應付 −）

現金是「當下餘額」，尚未扣未交割的買進款，故**必須**加未交割淨額才正確。

### 資料來源（實測欄位）

| | 元富 `taishin` | 玉山 `esun` |
|---|---|---|
| 持股 | `accounting.inventories(acc).position_summaries` | `sdk.get_inventories()` |
| 現金 | `accounting.bank_balance(acc)[0].available_balance` | `sdk.get_balance()["available_balance"]` |
| 未交割 | `accounting.history_settlement(acc, from, to).settlements[].net_amount`（濾 `s_date > 快照日`） | `sdk.get_settlements()[].price` |
| 持股市值 | `inventories().market_value`（帳戶層權威值） | Σ`value_mkt` |
| 未實現(帳戶) | `inventories().unrealized_profit_loss` | Σ`make_a_sum` |
| 未實現(逐檔) | `total_profit − realized_profit`（**`unrealized_profit` 是 None，勿用**） | `make_a_sum` |

- 數值皆字串需轉型；`_f()` 安全轉 float 防 None。損益率統一存小數（Sheet 以 % 顯示）。
- 元富 `settlement_net`/`settlement_today` 只看「今日」交割，**不含未來交割日**，故未交割要用 `history_settlement` 撈日期範圍再濾 `s_date > 快照日`。

## 費用計算邏輯（元富，已實測確認）

- 費用以**委託書號（order_no）為單位**計算，不是每筆部分成交各自計算後加總
- `fee = max(int(total_amount × 0.001425), fee_min)`
  - 整股最低：20 元；零股（`MarketType.IntradayOdd`）最低：**1 元**
- 截斷方式：`int()`（無條件捨去），非 `round()`
- 交易稅：`int(total_amount × 0.003)`，當沖賣出：`int(total_amount × 0.0015)`
- 費用只分配給同委託的第一筆 fill，其餘為 0；merge 後加總即為正確委託費用
