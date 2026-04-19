from abc import ABC, abstractmethod
from datetime import date
from typing import List

from models import Trade


class BrokerClient(ABC):
    """所有券商 client 的抽象介面。新增券商只需繼承此類並實作兩個方法。"""

    @property
    @abstractmethod
    def account_name(self) -> str:
        """顯示在 Google Sheets 股票帳戶欄的名稱，e.g. '元富'"""
        ...

    @abstractmethod
    def get_fills(self, target_date: date) -> List[Trade]:
        """
        回傳指定日期的所有成交紀錄。
        非交易日或無成交時回傳空 list。
        每筆 Trade.broker_account 必須填入 self.account_name。
        """
        ...
