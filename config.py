"""
設定模組。

支援多個 broker，每個 broker 以環境變數前綴區分：
  FUGLE_*   → 元富（fugle-trade SDK）

未來新增券商時，在 BROKER_REGISTRY 加入對應的 factory function
並新增對應的環境變數即可。
"""

import os
from dataclasses import dataclass
from typing import List

from brokers.base import BrokerClient


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(f"缺少必要環境變數：{key}")
    return val


def _build_fugle_broker() -> BrokerClient:
    from brokers.fugle import FugleBroker
    return FugleBroker(
        identity=_require("FUGLE_IDENTITY"),
        password=_require("FUGLE_PASSWD"),
        cert_path=_require("FUGLE_CERT_PATH"),
        cert_pass=_require("FUGLE_CERT_PASS"),
        account_name=os.environ.get("FUGLE_ACCOUNT_NAME", "元富"),
    )


def _build_esun_broker() -> BrokerClient:
    from brokers.esun import EsunBroker
    return EsunBroker(
        api_key=_require("ESUN_API_KEY"),
        api_secret=_require("ESUN_API_SECRET"),
        account=_require("ESUN_ACCOUNT"),
        account_password=_require("ESUN_PASSWD"),
        cert_path=_require("ESUN_CERT_PATH"),
        cert_pass=_require("ESUN_CERT_PASS"),
        entry=os.environ.get(
            "ESUN_API_ENTRY",
            "https://esuntradingapi.esunsec.com.tw/api/v1",
        ),
        account_name=os.environ.get("ESUN_ACCOUNT_NAME", "玉山"),
    )


# 新增券商：在此 dict 加入 "broker_id": factory_function
BROKER_REGISTRY = {
    "fugle": _build_fugle_broker,
    "esun": _build_esun_broker,
    # "ctbc":  _build_ctbc_broker,
}


def get_enabled_brokers() -> List[BrokerClient]:
    """依 ENABLED_BROKERS 環境變數（逗號分隔）回傳啟用的 broker 列表。"""
    enabled = os.environ.get("ENABLED_BROKERS", "fugle").split(",")
    brokers = []
    for name in enabled:
        name = name.strip()
        if name not in BROKER_REGISTRY:
            raise ValueError(f"未知的 broker：{name}，可用：{list(BROKER_REGISTRY)}")
        brokers.append(BROKER_REGISTRY[name]())
    return brokers


@dataclass
class SheetsConfig:
    service_account_json: str
    sheet_id: str
    sheet_name: str
    stock_info_tab: str   # FuturesTrade 寫入的股票名稱工作表名稱

    @classmethod
    def from_env(cls) -> "SheetsConfig":
        return cls(
            service_account_json=_require("GOOGLE_SERVICE_ACCOUNT_JSON"),
            sheet_id=_require("GOOGLE_SHEET_ID"),
            sheet_name=os.environ.get("GOOGLE_SHEET_NAME", "對帳單"),
            stock_info_tab=os.environ.get("GOOGLE_STOCK_INFO_TAB", "股票代號"),
        )
