# CLAUDE.md — trade-sync

## 專案目的

每個交易日收盤後（16:00 台灣時間），自動從各券商 API 抓取當日成交明細，
合併同一訂單的多筆部分成交後，append 寫入 Google Sheets 對帳表。

## 架構概覽

```
trade-sync/
├── main.py              # 執行入口（--date / --backfill-pnl）
├── config.py            # 環境變數、broker factory
├── models.py            # Trade dataclass + merge_fills()
├── sheets_client.py     # Google Sheets 讀寫（含不覆蓋邏輯、backfill）
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
| `ESUN_ACCOUNT`                | 玉山帳號                                  |
| `ESUN_PASSWORD`               | 玉山密碼                                  |
| `ESUN_CERT_PATH`              | 憑證路徑，e.g. `./certs/esun.p12`        |
| `ESUN_CERT_PASS`              | 憑證密碼                                  |
| `ESUN_ACCOUNT_NAME`           | Sheet 顯示名稱（預設：玉山）              |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service Account JSON 字串                |
| `GOOGLE_SHEET_ID`             | 試算表 ID                                 |
| `GOOGLE_SHEET_NAME`           | 工作表名稱（預設：對帳單）                |
| `STOCK_INFO_TAB`              | 股票代號 tab 名稱（預設：股票代號）       |

GitHub Actions 以 Secrets 注入。憑證需 base64 編碼後存為 `FUGLE_CERT_BASE64` / `ESUN_CERT_BASE64`：
```bash
base64 -i certs/fugle.pfx | pbcopy
```

## 執行

```bash
cp .env.example .env   # 填入真實憑證
pip install -r requirements.txt
python main.py                             # 今日
python main.py --date 2026-03-15           # 指定日期
python main.py --backfill-pnl 2026-04-17  # 補填指定日期的損益與平均成本
```

### --backfill-pnl 說明

當天 `realized_profit_and_loses` 尚未結算（批次處理，隔日才有資料），
成交當下寫入 Sheet 時 D（平均成本）和 J（已實現損益）會留空。
隔日執行此指令可補填：

- 掃 Sheet 找符合：日期 == 指定日期 + 帳戶 == 券商帳戶名稱 + 類型 in {賣出,當沖賣出} + J 欄為空
- 呼叫 `sdk.accounting.realized_profit_and_loses` 取得損益
- 按股數比例分配 PnL，`batch_update` 寫回 D 和 J 欄

## 費用計算邏輯（元富，已實測確認）

- 費用以**委託書號（order_no）為單位**計算，不是每筆部分成交各自計算後加總
- `fee = max(int(total_amount × 0.001425), fee_min)`
  - 整股最低：20 元；零股（`MarketType.IntradayOdd`）最低：**1 元**
- 截斷方式：`int()`（無條件捨去），非 `round()`
- 交易稅：`int(total_amount × 0.003)`，當沖賣出：`int(total_amount × 0.0015)`
- 費用只分配給同委託的第一筆 fill，其餘為 0；merge 後加總即為正確委託費用
