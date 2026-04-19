"""
元富/台新證券 broker client（taishin_sdk v1.0.2）

成交查詢：sdk.stock.filled_history(acc, "YYYYMMDD", "YYYYMMDD")
損益查詢：sdk.accounting.realized_profit_and_loses(acc, "YYYYMMDD", "YYYYMMDD")

FilledRecordOutput 欄位：
  symbol        str    股票代號
  buy_sell      BSAction enum  Buy / Sell
  filled_price  float  成交價
  filled_qty    int    股數
  filled_date   str    成交日期 YYYYMMDD
  filled_time   str    成交時間
  payment       float  price × qty（不含手續費）
  order_type    OrderType enum  Stock / Margin / Short / DayTradeShort / Unsupport
  order_no      str    委託書號

RealizedProfitLossSummary 欄位（實測確認）：
  symbol             str    股票代號
  closed_quantity    str    賣出股數
  profit_loss        str    已實現損益（含手續費與交易稅）
  buy_detail.filled_price  str  買入均價（不含手續費，對應 D 欄平均成本）

注意：
  - 當沖先賣後買：order_type == OrderType.DayTradeShort
  - 當沖先買後賣：API 無標記，依同日同股有買有賣推斷
  - payment 不含費用，費用從標準費率計算
"""

from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

from taishin_sdk import BSAction, MarketType, OrderType  # MarketType.IntradayOdd = 零股盤中

from brokers.base import BrokerClient
from models import Trade
import stock_names

_FEE_RATE = 0.001425
_FEE_MIN = 20
_TAX_RATE_NORMAL = 0.003
_TAX_RATE_DAYTRADE = 0.0015


def _detect_daytrades(raw_fills) -> Tuple[set, set]:
    """
    偵測先買後賣當沖（API 不標記，需自行推斷）。

    規則：同日同股若同時有 Buy 和非 DayTradeShort 的 Sell，
    則 Sell 判定為當沖賣出，對應 Buy 以「成交價最接近賣出均價」優先標記為當沖買入。

    回傳 (daytrade_buy_ids, daytrade_sell_ids)：fill 的 id()。
    """
    groups = defaultdict(lambda: {"buys": [], "sells": []})
    for fill in raw_fills:
        key = (fill.filled_date, str(fill.symbol))
        if fill.buy_sell == BSAction.Buy:
            groups[key]["buys"].append(fill)
        elif fill.order_type != OrderType.DayTradeShort:
            groups[key]["sells"].append(fill)

    daytrade_buy_ids: set = set()
    daytrade_sell_ids: set = set()

    for g in groups.values():
        if not g["buys"] or not g["sells"]:
            continue

        buy_qty = sum(f.filled_qty for f in g["buys"])
        sell_qty = sum(f.filled_qty for f in g["sells"])
        # 配對數量取買賣較小的那方（多餘的是一般倉位買進或賣出）
        daytrade_qty = min(buy_qty, sell_qty)

        avg_buy_price = (
            sum(f.filled_price * f.filled_qty for f in g["buys"]) / buy_qty
        )
        avg_sell_price = (
            sum(f.filled_price * f.filled_qty for f in g["sells"]) / sell_qty
        )

        # 賣方：以價格最接近買入均價者優先標為當沖，直到配對數量滿
        sells_sorted = sorted(
            g["sells"], key=lambda f: abs(f.filled_price - avg_buy_price)
        )
        remaining = daytrade_qty
        for f in sells_sorted:
            if remaining <= 0:
                break
            daytrade_sell_ids.add(id(f))
            remaining -= f.filled_qty

        # 買方：以價格最接近賣出均價者優先標為當沖，直到配對數量滿
        buys_sorted = sorted(
            g["buys"], key=lambda f: abs(f.filled_price - avg_sell_price)
        )
        remaining = daytrade_qty
        for f in buys_sorted:
            if remaining <= 0:
                break
            daytrade_buy_ids.add(id(f))
            remaining -= f.filled_qty

    return daytrade_buy_ids, daytrade_sell_ids


def _build_pnl_lookup(response) -> Dict[str, dict]:
    """
    將 realized_profit_and_loses 回傳值整理成 {symbol: {pnl, avg_buy_price, qty}}。

    - pnl：已實現損益（int，含手續費與交易稅）
    - avg_buy_price：買入均價（float，不含手續費，用於 D 欄平均成本）
    - qty：賣出總股數（用於按比例分配損益到個別 fill）
    """
    lookup: Dict[str, dict] = {}
    for s in response.profit_loss_summary:
        sym = str(s.symbol)
        pnl = int(s.profit_loss)
        qty = int(s.closed_quantity)
        buy_price = float(s.buy_detail.filled_price)

        if sym not in lookup:
            lookup[sym] = {"pnl": 0, "buy_price_sum": 0.0, "qty": 0}
        lookup[sym]["pnl"] += pnl
        lookup[sym]["buy_price_sum"] += buy_price * qty
        lookup[sym]["qty"] += qty

    return {
        sym: {
            "pnl": d["pnl"],
            "avg_buy_price": d["buy_price_sum"] / d["qty"],
            "qty": d["qty"],
        }
        for sym, d in lookup.items()
    }


class FugleBroker(BrokerClient):
    def __init__(
        self,
        identity: str,
        password: str,
        cert_path: str,
        cert_pass: str,
        account_name: str = "元富",
    ):
        self._identity = identity
        self._password = password
        self._cert_path = cert_path
        self._cert_pass = cert_pass
        self._account_name = account_name
        self._sdk = None
        self._acc = None

    @property
    def account_name(self) -> str:
        return self._account_name

    def _connect(self):
        if self._sdk is not None:
            return

        from taishin_sdk import TaishinSDK
        sdk = TaishinSDK()
        accounts = sdk.login(
            self._identity,
            self._password,
            self._cert_path,
            self._cert_pass,
        )
        self._sdk = sdk
        self._acc = accounts[0]

    def _fetch_pnl_lookup(self, date_str: str) -> Dict[str, dict]:
        """查詢當日已實現損益，失敗時回傳空字典（不中斷主流程）。"""
        try:
            resp = self._sdk.accounting.realized_profit_and_loses(
                self._acc, date_str, date_str
            )
            return _build_pnl_lookup(resp)
        except Exception as e:
            print(f"[元富] 損益查詢失敗（{e}），平均成本與損益欄位留空。")
            return {}

    def fetch_realized_pnl(self, target_date: date) -> Dict[str, dict]:
        """
        查詢指定日期的已實現損益，供 backfill 使用。
        回傳 {symbol: {pnl, avg_buy_price, qty}}；查無資料則回傳空字典。
        """
        self._connect()
        date_str = target_date.strftime("%Y%m%d")
        return self._fetch_pnl_lookup(date_str)

    def get_fills(self, target_date: date) -> List[Trade]:
        self._connect()
        date_str = target_date.strftime("%Y%m%d")
        # 當天成交用 get_filled()，歷史用 filled_history()
        # （filled_history 當天返回空，需隔日才有資料）
        if target_date == date.today():
            raw = self._sdk.stock.get_filled(self._acc)
        else:
            raw = self._sdk.stock.filled_history(self._acc, date_str, date_str)

        # 偵測先買後賣當沖
        daytrade_buy_ids, daytrade_sell_ids = _detect_daytrades(raw)

        # 查詢已實現損益（含買入均價）
        pnl_lookup = self._fetch_pnl_lookup(date_str)

        # ── 按 order_no 計算委託單層級的手續費與交易稅 ────────────────────
        # 費用以整筆委託金額計算（int 截斷），而非每筆部分成交各自計算後加總，
        # 否則同一委託的多筆部分成交各自套用最低費會重複計算。
        order_amounts: Dict[str, float] = defaultdict(float)
        order_is_odd: Dict[str, bool] = defaultdict(bool)
        order_side: Dict[str, str] = {}

        for fill in raw:
            ono = fill.order_no
            order_amounts[ono] += fill.filled_price * fill.filled_qty
            if fill.market_type == MarketType.IntradayOdd:
                order_is_odd[ono] = True
            # side（先確定，後面 loop 再用）
            if fill.buy_sell == BSAction.Buy:
                s = "DaytradeBuy" if id(fill) in daytrade_buy_ids else "Buy"
            elif fill.order_type == OrderType.DayTradeShort:
                s = "DaytradeSell"
            elif id(fill) in daytrade_sell_ids:
                s = "DaytradeSell"
            else:
                s = "Sell"
            order_side[ono] = s

        order_fees: Dict[str, int] = {}
        order_taxes: Dict[str, int] = {}
        for ono, total_amt in order_amounts.items():
            fee_min = 1 if order_is_odd[ono] else 20
            order_fees[ono] = max(int(total_amt * _FEE_RATE), fee_min)
            s = order_side[ono]
            if s in ("Sell", "DaytradeSell"):
                tax_rate = _TAX_RATE_DAYTRADE if s == "DaytradeSell" else _TAX_RATE_NORMAL
                order_taxes[ono] = int(total_amt * tax_rate)
            else:
                order_taxes[ono] = 0

        # 費用只分配給同一委託的第一筆成交，其餘為 0（合併後加總即為委託費用）
        order_first_seen: set = set()
        # ─────────────────────────────────────────────────────────────────

        trades: List[Trade] = []
        for fill in raw:
            symbol: str = str(fill.symbol)
            ono = fill.order_no

            side = order_side[ono]
            price: float = fill.filled_price
            quantity: int = fill.filled_qty
            amount: float = price * quantity

            is_first = ono not in order_first_seen
            order_first_seen.add(ono)
            fee = order_fees[ono] if is_first else 0
            tax = order_taxes[ono] if is_first else 0

            filled_date_raw = str(fill.filled_date)
            try:
                fill_date = date(
                    int(filled_date_raw[:4]),
                    int(filled_date_raw[4:6]),
                    int(filled_date_raw[6:8]),
                )
            except (ValueError, IndexError):
                fill_date = target_date

            avg_cost: float = 0.0
            pnl: Optional[float] = None
            if side in ("Sell", "DaytradeSell") and symbol in pnl_lookup:
                data = pnl_lookup[symbol]
                avg_cost = data["avg_buy_price"]
                pnl = round(data["pnl"] * quantity / data["qty"])

            stock_name: str = stock_names.get(symbol)
            is_odd_lot: bool = fill.market_type == MarketType.IntradayOdd

            trades.append(Trade(
                trade_date=fill_date,
                stock_id=symbol,
                stock_name=stock_name,
                side=side,
                price=price,
                quantity=quantity,
                amount=amount,
                fee=fee,
                tax=tax,
                broker_account=self._account_name,
                avg_cost=avg_cost,
                pnl=pnl,
                is_odd_lot=is_odd_lot,
            ))

        return trades
