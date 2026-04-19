# trade-sync

每個交易日收盤後，自動從各券商 API 抓取當日成交明細，寫入 Google Sheets 對帳表。

## 功能

- 自動抓取成交明細（支援元富/台新、玉山）
- 合併同一委託的多筆部分成交（加權均價、費用正確加總）
- 自動計算手續費與交易稅（依委託書號計算，符合對帳單）
- 識別當沖交易（先賣後買、先買後賣）
- 不覆蓋已寫入的資料（依帳戶最後記錄日期判斷）
- 隔日補填損益與平均成本（`--backfill-pnl`）

## 安裝

```bash
pip install -r requirements.txt
```

> taishin_sdk 和 esun_trade 不在 PyPI，需手動安裝 .whl：
> ```bash
> pip install taishin_sdk-1.0.2-*.whl
> pip install esun_trade-2.2.0-*.whl
> ```

## 設定

```bash
cp .env.example .env
```

編輯 `.env`，填入以下變數：

| 變數 | 說明 |
|------|------|
| `ENABLED_BROKERS` | 啟用的券商，逗號分隔，e.g. `fugle,esun` |
| `FUGLE_IDENTITY` | 元富身分證字號 |
| `FUGLE_PASSWD` | 元富登入密碼 |
| `FUGLE_CERT_PATH` | 元富憑證路徑，e.g. `./certs/fugle.pfx` |
| `FUGLE_CERT_PASS` | 元富憑證密碼 |
| `ESUN_ACCOUNT` | 玉山帳號 |
| `ESUN_PASSWORD` | 玉山密碼 |
| `ESUN_CERT_PATH` | 玉山憑證路徑，e.g. `./certs/esun.p12` |
| `ESUN_CERT_PASS` | 玉山憑證密碼 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Service Account JSON 字串 |
| `GOOGLE_SHEET_ID` | Google Sheets 試算表 ID |
| `GOOGLE_SHEET_NAME` | 工作表名稱（預設：對帳單） |
| `STOCK_INFO_TAB` | 股票代號對照 tab 名稱（預設：股票代號） |

憑證放在 `certs/` 目錄下（已列入 .gitignore）。

macOS 需移除憑證的 quarantine：
```bash
xattr -d com.apple.macl ./certs/fugle.pfx
```

## 執行

```bash
# 抓今日成交，寫入 Sheet
python main.py

# 指定日期
python main.py --date 2026-04-17

# 補填指定日期的損益與平均成本（隔日才能查到結算資料）
python main.py --backfill-pnl 2026-04-17
```

### 正常流程

1. **收盤後（16:00 後）** 執行 `python main.py`
   - 成交明細寫入 Sheet
   - 賣出損益需隔日才能從 API 取得，暫時留空

2. **隔日** 執行 `python main.py --backfill-pnl <日期>`
   - 補填昨日賣出列的平均成本（D 欄）和已實現損益（J 欄）

## Google Sheets 欄位

| 欄 | 名稱 | 說明 |
|----|------|------|
| A | 交易日期 | YYYY/MM/DD |
| B | 交易類型 | 買進 / 賣出 / 當沖買入 / 當沖賣出 |
| C | 股名 | 代號 + 名稱，e.g. `6770 力積電` |
| D | 平均成本 | 持倉成本（賣出隔日補填） |
| E | 成交價 | 多筆部分成交時為加權均價 |
| F | 股數 | |
| G | 成交金額 | |
| H | 手續費 | 與對帳單一致 |
| I | 交易稅 | 賣出才有 |
| J | 已實現損益 | 賣出隔日補填 |
| K | 已實現損益率 | Sheet 公式自動計算 |
| L | 融券融資費 | |
| M | 股票帳戶 | 元富 / 玉山 |
| N | 策略 | 手動填 |
| O | 筆記 | 手動填 |

## 新增券商

1. 在 `brokers/` 建立新檔案，繼承 `BrokerClient`，實作 `account_name` 和 `get_fills()`
2. 在 `config.py` 的 `BROKER_REGISTRY` 加入 factory function
3. 更新 `.env.example`

## GitHub Actions 自動排程

平日 16:05 台灣時間自動執行。憑證以 base64 存為 Secrets：

```bash
base64 -i certs/fugle.pfx | pbcopy  # 複製後貼到 FUGLE_CERT_BASE64
base64 -i certs/esun.p12 | pbcopy   # 複製後貼到 ESUN_CERT_BASE64
```
