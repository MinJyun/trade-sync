"""中信（中國信託綜合證券）月對帳單 PDF 解析。

中信無對外 API（下單/查詢需其 Windows 專用程式），故改用每月寄送的
「綜合月對帳單」PDF 匯入。本模組解析「國內有價證券交易明細」表格，
把每一筆成交轉成 Trade 物件，供 main.py 寫入 Google Sheets。

PDF 為加密檔，開啟密碼為身分證字號（由呼叫端提供）。

對帳單表格欄位（實測）：
  交易日期 交易管道 交易別 商品代號 商品名稱 股數 成交價格 成交金額 手續費 [交易稅] 應收付金額
  - 「現買」等買進列沒有「交易稅」欄；「現賣」等賣出列才有。
  - 金額、手續費、交易稅皆直接採用對帳單數字（對帳單為最終結算值）。
"""

import re
from datetime import date
from typing import List, Optional

import pdfplumber

import stock_names
from models import Trade

# 交易明細列以日期開頭（YYYY/MM/DD）；其餘（表頭、庫存、附註）皆非此格式。
_DATE_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})\b")
# 純數字欄位：整數（可含千分位逗號）或小數，可帶負號。
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_statement(
    pdf_path: str, password: str, account_name: str = "中信"
) -> List[Trade]:
    """解析對帳單 PDF，回傳交易明細的 Trade 列表（未合併、未過濾日期）。"""
    trades: List[Trade] = []
    with pdfplumber.open(pdf_path, password=password) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                t = _parse_line(line.strip(), account_name)
                if t is not None:
                    trades.append(t)
    return trades


def _parse_line(line: str, account_name: str) -> Optional[Trade]:
    m = _DATE_RE.match(line)
    if not m:
        return None

    tokens = line.split()
    # tokens 佈局：[日期, 交易管道, 交易別, 商品代號, 商品名稱..., <數字欄...>]
    if len(tokens) < 8:
        return None

    trade_type = tokens[2]          # 現買 / 現賣 / 資買 / 券賣 ...
    if "買" in trade_type:
        side, n_num = "Buy", 5      # 股數,成交價,成交金額,手續費,應收付金額
    elif "賣" in trade_type:
        side, n_num = "Sell", 6     # 股數,成交價,成交金額,手續費,交易稅,應收付金額
    else:
        print(f"[ctbc] 未知交易別「{trade_type}」，略過：{line}")
        return None

    numerics = tokens[-n_num:]
    if not all(_NUM_RE.match(x) for x in numerics):
        print(f"[ctbc] 數字欄位格式不符，略過：{line}")
        return None

    code = tokens[3]
    stmt_name = "".join(tokens[4:-n_num])   # 商品名稱（中文，通常單一 token）

    trade_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    quantity = int(_num(numerics[0]))
    price = _num(numerics[1])
    amount = _num(numerics[2])
    fee = _num(numerics[3])
    tax = _num(numerics[4]) if side == "Sell" else 0.0

    # 股名：優先用 Sheet「股票代號」對照表，查無則用對帳單上的（可能簡稱）名稱。
    name = stock_names.get(code)
    if name == code and stmt_name:
        name = f"{code} {stmt_name}"

    return Trade(
        trade_date=trade_date,
        stock_id=code,
        stock_name=name,
        side=side,
        price=price,
        quantity=quantity,
        amount=amount,
        fee=fee,
        tax=tax,
        broker_account=account_name,
    )
