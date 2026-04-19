from dataclasses import dataclass, field
from datetime import date
from typing import Optional, List

# 交易類型對照
SIDE_LABEL = {
    "Buy": "買進",
    "Sell": "賣出",
    "DaytradeBuy": "當沖買入",
    "DaytradeSell": "當沖賣出",
}


@dataclass
class Trade:
    """
    單筆成交紀錄（來自各券商 API）
    合併同一訂單的多筆部分成交後產生。
    """
    trade_date: date
    stock_id: str           # 股票代號，e.g. "6770"
    stock_name: str         # 顯示名稱，e.g. "6770 力積電"
    side: str               # "Buy" | "Sell" | "DaytradeBuy" | "DaytradeSell"
    price: float            # 成交價（多筆部分成交時為加權均價）
    quantity: int           # 股數
    amount: float           # 成交金額
    fee: float              # 手續費
    tax: float              # 交易稅
    broker_account: str     # 券商帳戶名稱，e.g. "元富"

    # 以下欄位由 API 提供時填入，否則留預設值
    avg_cost: float = 0.0               # 平均成本（持倉成本，買進為 0）
    pnl: Optional[float] = None        # 已實現損益
    pnl_rate: Optional[float] = None   # 已實現損益率（小數，0.05 = 5%）
    margin_fee: float = 0.0            # 融券融資費
    is_odd_lot: bool = False           # 是否為零股交易（最低手續費 1 元）

    def to_row(self) -> list:
        """轉成 Google Sheets 單列，共 15 欄。"""
        pnl_str = round(self.pnl, 0) if self.pnl is not None else ""
        margin_str = round(self.margin_fee, 0) if self.margin_fee else ""

        return [
            self.trade_date.strftime("%Y/%m/%d"),   # A 交易日期
            SIDE_LABEL.get(self.side, self.side),   # B 交易類型
            self.stock_name,                         # C 股名
            round(self.avg_cost, 2),                 # D 平均成本
            round(self.price, 2),                    # E 成交價
            self.quantity,                           # F 股數
            round(self.amount, 0),                   # G 成交金額
            round(self.fee, 0),                      # H 手續費
            round(self.tax, 0),                      # I 交易稅
            pnl_str,                                 # J 已實現損益（API 提供時填入）
            "",                                      # K 已實現損益率（Sheet 公式計算，留空）
            margin_str,                              # L 融券融資費
            self.broker_account,                     # M 股票帳戶
            "",                                      # N 策略
            "",                                      # O 筆記
        ]


def merge_fills(fills: List[Trade]) -> List[Trade]:
    """
    將來自 API 的多筆部分成交（partial fills）合併成一筆。
    合併條件：同日期、同股票、同方向、同帳戶。

    - 成交價、平均成本：加權平均
    - 股數、金額、手續費、交易稅、損益：加總
    - 損益率：合併後重新計算
    """
    from collections import defaultdict

    groups: dict = defaultdict(list)
    for t in fills:
        key = (t.trade_date, t.stock_id, t.side, t.broker_account)
        groups[key].append(t)

    result: List[Trade] = []
    for (trade_date, stock_id, side, broker_account), group in sorted(
        groups.items(), key=lambda x: (x[0][0], x[0][3], x[0][2])
    ):
        total_qty = sum(t.quantity for t in group)
        total_amount = sum(t.amount for t in group)
        avg_price = total_amount / total_qty if total_qty else 0.0

        # 加權平均持倉成本（通常每筆相同，但保險起見加權計算）
        avg_cost = (
            sum(t.avg_cost * t.quantity for t in group) / total_qty
            if total_qty else 0.0
        )

        total_pnl = (
            sum(t.pnl for t in group if t.pnl is not None)
            if any(t.pnl is not None for t in group) else None
        )

        # 損益率 = 損益 / (持倉成本 × 股數)
        pnl_rate = None
        if total_pnl is not None and avg_cost > 0 and total_qty > 0:
            pnl_rate = total_pnl / (avg_cost * total_qty)

        result.append(Trade(
            trade_date=trade_date,
            stock_id=stock_id,
            stock_name=group[0].stock_name,
            side=side,
            price=avg_price,
            quantity=total_qty,
            amount=total_amount,
            fee=sum(t.fee for t in group),
            tax=sum(t.tax for t in group),
            broker_account=broker_account,
            avg_cost=avg_cost,
            pnl=total_pnl,
            pnl_rate=pnl_rate,
            margin_fee=sum(t.margin_fee for t in group),
        ))

    return result
