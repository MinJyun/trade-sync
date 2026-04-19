"""
玉山證券 broker client（esun_trade SDK v2.2.0）

API：
  sdk.get_transactions("0d")                      → 今日成交
  sdk.get_transactions_by_date("yyyy-MM-dd", "yyyy-MM-dd") → 歷史成交

回傳結構（實測確認）：
  外層（每檔股票匯總）：
    stk_no      股票代號
    stk_na      股票名稱
    buy_sell    "B" / "S"
    t_date      成交日期 (YYYYMMDD)
    c_date      交割日 (YYYYMMDD)
    price_avg   平均成交價（字串）
    qty         總股數（字串）
    price_qty   成交金額（字串）
    cost        持倉成本（字串，買進為 "0"）
    make        已實現損益（字串）
    make_per    已實現損益率（字串，百分比值）
    mat_dats    成交明細列表

  內層 mat_dats：
    fee         手續費（字串）
    tax         交易稅（字串）
    db_fee      融資融券費（字串）
    pay_n       應收付金額（字串，負數為支出）

注意：所有數值皆為字串，需轉型。
"""

import os
from configparser import ConfigParser
from datetime import date
from typing import List

from keyring import set_password

from brokers.base import BrokerClient
from models import Trade

_ACCOUNT_KEY = "esun_trade_sdk:account"
_CERT_KEY = "esun_trade_sdk:cert"

_BUY_SELL_MAP = {
    "B": "Buy",
    "S": "Sell",
}


class EsunBroker(BrokerClient):
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        account: str,
        account_password: str,
        cert_path: str,
        cert_pass: str,
        entry: str = "https://esuntradingapi.esunsec.com.tw/api/v1",
        account_name: str = "玉山",
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._account = account
        self._account_password = account_password
        self._cert_path = cert_path
        self._cert_pass = cert_pass
        self._entry = entry
        self._account_name = account_name
        self._sdk = None

    @property
    def account_name(self) -> str:
        return self._account_name

    def _connect(self):
        if self._sdk is not None:
            return self._sdk

        from esun_trade.sdk import SDK

        # 寫入 keyring 確保 login() 不需互動式輸入
        set_password(_ACCOUNT_KEY, self._account, self._account_password)
        set_password(_CERT_KEY, self._account, self._cert_pass)

        config = ConfigParser()
        config.read_string(
            f"[Core]\n"
            f"Entry = {self._entry}\n\n"
            f"[Cert]\n"
            f"Path = {self._cert_path}\n\n"
            f"[Api]\n"
            f"Key = {self._api_key}\n"
            f"Secret = {self._api_secret}\n\n"
            f"[User]\n"
            f"Account = {self._account}\n"
        )

        sdk = SDK(config)
        sdk.login()
        self._sdk = sdk
        return sdk

    def get_fills(self, target_date: date) -> List[Trade]:
        sdk = self._connect()
        date_str = target_date.strftime("%Y-%m-%d")

        raw = sdk.get_transactions_by_date(date_str, date_str)

        trades: List[Trade] = []
        for item in raw:
            stk_no: str = item.get("stk_no", "")
            stk_na: str = item.get("stk_na", "")
            stock_name = f"{stk_no} {stk_na}" if stk_na else stk_no

            buy_sell: str = item.get("buy_sell", "B")
            side: str = _BUY_SELL_MAP.get(buy_sell, "Buy")

            price: float = float(item.get("price_avg", 0))
            quantity: int = int(item.get("qty", 0))
            amount: float = float(item.get("price_qty", 0))

            # fee/tax/db_fee 在 mat_dats 內，加總
            mat_dats: list = item.get("mat_dats", [])
            total_fee: float = sum(float(d.get("fee", 0)) for d in mat_dats)
            total_tax: float = sum(float(d.get("tax", 0)) for d in mat_dats)
            total_db_fee: float = sum(float(d.get("db_fee", 0)) for d in mat_dats)

            # 持倉成本：API 回傳負數（支出方向），除以股數得每股均價
            # e.g. cost="-1111", qty=1 → avg_cost = 1111.0
            cost_raw = float(item.get("cost", 0))
            avg_cost: float = abs(cost_raw) / quantity if quantity and cost_raw != 0 else 0.0

            # 已實現損益
            pnl_raw = item.get("make", "0")
            pnl: float = float(pnl_raw) if pnl_raw != "0" else 0.0
            pnl = pnl if pnl != 0 or side == "Sell" else None

            pnl_rate_raw = item.get("make_per", "0.00")
            pnl_rate: float = float(pnl_rate_raw) / 100 if pnl_raw != "0" else None

            # 成交日期
            t_date_str: str = item.get("t_date", "")
            try:
                fill_date = date(
                    int(t_date_str[:4]),
                    int(t_date_str[4:6]),
                    int(t_date_str[6:8]),
                )
            except (ValueError, IndexError):
                fill_date = target_date

            trades.append(Trade(
                trade_date=fill_date,
                stock_id=stk_no,
                stock_name=stock_name,
                side=side,
                price=price,
                quantity=quantity,
                amount=amount,
                fee=total_fee,
                tax=total_tax,
                broker_account=self._account_name,
                avg_cost=avg_cost,
                pnl=pnl,
                pnl_rate=pnl_rate,
                margin_fee=total_db_fee,
            ))

        return trades
