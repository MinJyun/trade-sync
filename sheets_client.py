"""
Google Sheets 讀寫封裝。

核心邏輯：
- 讀取現有資料，依帳戶找出各帳戶最後記錄日期
- append 時只寫入尚未記錄的資料（不覆蓋）
"""

import json
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from config import SheetsConfig

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# 欄位索引（0-based）
COL_DATE = 0     # A 交易日期
COL_ACCOUNT = 12  # M 股票帳戶


def _build_client(cfg: SheetsConfig) -> gspread.Client:
    info = json.loads(cfg.service_account_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def _parse_date(s: str) -> Optional[date]:
    """解析 YYYY/MM/DD 或 YYYY/M/D 格式（容許月日無補零），失敗回傳 None。"""
    try:
        parts = s.strip().replace("/", "-").split("-")
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, AttributeError, IndexError):
        pass
    return None


def get_last_dates(cfg: SheetsConfig) -> Dict[str, Optional[date]]:
    """
    讀取 sheet，回傳各帳戶最後記錄日期。
    e.g. {"元富": date(2026, 4, 2), "中信": date(2026, 3, 15)}
    """
    client = _build_client(cfg)
    sheet = client.open_by_key(cfg.sheet_id).worksheet(cfg.sheet_name)
    all_values = sheet.get_all_values()

    last_dates: Dict[str, Optional[date]] = defaultdict(lambda: None)

    for row in all_values[1:]:  # 跳過標題列
        if len(row) <= max(COL_DATE, COL_ACCOUNT):
            continue
        d = _parse_date(row[COL_DATE])
        account = row[COL_ACCOUNT].strip()
        if d and account:
            current = last_dates[account]
            if current is None or d > current:
                last_dates[account] = d

    return dict(last_dates)


def find_sell_rows_to_backfill(
    cfg: SheetsConfig, target_date: date, account: str
) -> List[dict]:
    """
    掃描 sheet，找出符合以下條件的列：
      - A 欄日期 == target_date
      - M 欄帳戶 == account
      - B 欄交易類型 in {賣出, 當沖賣出}
      - J 欄已實現損益為空

    回傳 [{"row": 1-based行號, "symbol": "6770", "quantity": 1000}, ...]
    """
    client = _build_client(cfg)
    sheet = client.open_by_key(cfg.sheet_id).worksheet(cfg.sheet_name)
    all_values = sheet.get_all_values()

    SELL_TYPES = {"賣出", "當沖賣出"}
    COL_TYPE = 1   # B
    COL_STOCK = 2  # C
    COL_QTY = 5    # F
    COL_PNL = 9    # J

    results = []
    for i, row in enumerate(all_values[1:], start=2):  # 1-based, skip header
        if len(row) <= max(COL_DATE, COL_ACCOUNT, COL_PNL):
            continue
        d = _parse_date(row[COL_DATE])
        if d != target_date:
            continue
        if row[COL_ACCOUNT].strip() != account:
            continue
        if row[COL_TYPE].strip() not in SELL_TYPES:
            continue
        if row[COL_PNL].strip():  # 已有損益，跳過
            continue

        stock_cell = row[COL_STOCK].strip()
        symbol = stock_cell.split()[0] if stock_cell else ""
        try:
            qty = int(row[COL_QTY].replace(",", "").strip())
        except (ValueError, IndexError):
            qty = 0

        if symbol and qty > 0:
            results.append({"row": i, "symbol": symbol, "quantity": qty})

    return results


def batch_update_pnl(cfg: SheetsConfig, updates: List[dict]) -> None:
    """
    批次更新 D 欄（平均成本）和 J 欄（已實現損益）。
    updates: [{"row": 行號, "avg_cost": float, "pnl": float}, ...]
    """
    if not updates:
        return

    client = _build_client(cfg)
    sheet = client.open_by_key(cfg.sheet_id).worksheet(cfg.sheet_name)

    cell_list = []
    for u in updates:
        r = u["row"]
        # D 欄 = col 4, J 欄 = col 10 (1-based)
        cell_list.append({"range": f"D{r}", "values": [[round(u["avg_cost"], 2)]]})
        cell_list.append({"range": f"J{r}", "values": [[round(u["pnl"], 0)]]})

    sheet.batch_update(cell_list, value_input_option="USER_ENTERED")
    print(f"[sheets] backfill 更新 {len(updates)} 列的平均成本與損益。")


def append_rows(cfg: SheetsConfig, rows: List[list]) -> None:
    """將多列資料寫到 sheet 中最後一筆有日期的列之後。

    不使用 gspread.append_rows()，避免 Sheet 中有公式的空列導致插入位置錯誤。
    """
    if not rows:
        return

    client = _build_client(cfg)
    sheet = client.open_by_key(cfg.sheet_id).worksheet(cfg.sheet_name)

    # 掃欄 A，找最後一筆有日期值的列（1-based row number）
    col_a = sheet.col_values(COL_DATE + 1)  # gspread col_values 從 1 開始
    last_data_row = 0
    for i, val in enumerate(col_a):
        if val.strip():
            last_data_row = i + 1  # 1-based

    next_row = last_data_row + 1
    last_row = next_row + len(rows) - 1

    # 欄 A–J（交易日期～已實現損益），跳過 K（損益率由 Sheet 公式計算）
    sheet.update(
        f"A{next_row}:J{last_row}",
        [r[:10] for r in rows],
        value_input_option="USER_ENTERED",
    )
    # 欄 L–O（融券融資費、帳戶、策略、筆記）
    sheet.update(
        f"L{next_row}:O{last_row}",
        [r[11:] for r in rows],
        value_input_option="USER_ENTERED",
    )
    print(f"[sheets] 已寫入 {len(rows)} 列（從第 {next_row} 列開始）")
