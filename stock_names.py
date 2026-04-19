"""
股票名稱對照表

從 Google Sheets 讀取 FuturesTrade 已維護的股票名稱資料。
回傳格式：{stock_id: "代號 名稱"}，e.g. {"2330": "2330 台積電"}

Sheet 欄位預期（與 FuturesTrade InfoManager 一致）：
  A: 有價證券代號及名稱  e.g. "2330 台積電"
  B: 證券代號            e.g. "2330"
  C: 股票名稱            e.g. "台積電"
  ...（後續欄位不使用）
"""

import json
from typing import Dict, Optional

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# module-level cache，避免重複讀取 API
_cache: Optional[Dict[str, str]] = None


def _build_client(service_account_json: str) -> gspread.Client:
    info = json.loads(service_account_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def load(service_account_json: str, sheet_id: str, tab_name: str) -> Dict[str, str]:
    """
    從 Google Sheets 載入股票名稱對照表，回傳 {stock_id: "代號 名稱"}。
    結果快取在 module level，同一執行期只讀取一次。
    """
    global _cache
    if _cache is not None:
        return _cache

    client = _build_client(service_account_json)
    ws = client.open_by_key(sheet_id).worksheet(tab_name)
    rows = ws.get_all_values()

    mapping: Dict[str, str] = {}
    for row in rows[1:]:  # 跳過標題列
        if len(row) < 2:
            continue
        stock_id = row[1].strip()   # 證券代號（欄 B）
        stock_name = row[2].strip() if len(row) > 2 else ""  # 股票名稱（欄 C）
        if stock_id:
            mapping[stock_id] = f"{stock_id} {stock_name}" if stock_name else stock_id

    _cache = mapping
    print(f"[stock_names] 載入 {len(mapping)} 筆股票名稱")
    return _cache


def get(stock_id: str, fallback: Optional[Dict[str, str]] = None) -> str:
    """
    查詢股票名稱。
    - 若 cache 已載入，直接查詢
    - 找不到時回傳代號本身（e.g. "2330"）
    """
    source = _cache if _cache is not None else (fallback or {})
    return source.get(stock_id, stock_id)
