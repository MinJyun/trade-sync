"""
trade-sync 執行入口

用法：
  python main.py                           # 抓今日，所有啟用的 broker
  python main.py --date 2026-03-15         # 指定日期
  python main.py --backfill-pnl 2026-04-17 # 補填指定日期的損益/平均成本
"""

import argparse
import sys
from datetime import date, timedelta
from typing import List

from dotenv import load_dotenv
load_dotenv()

from config import SheetsConfig, get_enabled_brokers
from models import Trade, merge_fills
from sheets_client import append_rows, get_last_dates, find_sell_rows_to_backfill, batch_update_pnl
import stock_names


def parse_args():
    parser = argparse.ArgumentParser(description="Sync broker fills to Google Sheets")
    parser.add_argument("--date", type=str, default=None, help="目標日期 YYYY-MM-DD")
    parser.add_argument("--backfill-pnl", type=str, default=None, metavar="DATE",
                        help="補填指定日期（YYYY-MM-DD）賣出列的損益與平均成本")
    return parser.parse_args()


def run_backfill_pnl(target_date: date, sheets_cfg: SheetsConfig):
    """掃描 sheet 中損益為空的賣出列，從 API 補填平均成本與已實現損益。"""
    brokers = get_enabled_brokers()

    for broker in brokers:
        account = broker.account_name
        print(f"[{account}] backfill {target_date} 損益中...")

        rows = find_sell_rows_to_backfill(sheets_cfg, target_date, account)
        if not rows:
            print(f"[{account}] 無需補填的列。")
            continue

        print(f"[{account}] 找到 {len(rows)} 列待補填：{[r['symbol'] for r in rows]}")

        # 取得 PnL lookup（有可能前一天也需要查）
        try:
            pnl_lookup = broker.fetch_realized_pnl(target_date)
        except AttributeError:
            print(f"[{account}] 此券商不支援 fetch_realized_pnl，跳過。")
            continue
        except Exception as e:
            print(f"[{account}] 損益查詢失敗：{e}")
            continue

        if not pnl_lookup:
            print(f"[{account}] API 查無損益資料（可能尚未結算），請稍後再試。")
            continue

        updates = []
        for row_info in rows:
            sym = row_info["symbol"]
            qty = row_info["quantity"]

            if sym not in pnl_lookup:
                print(f"  [{sym}] 損益查無此股，略過。")
                continue

            data = pnl_lookup[sym]
            pnl_for_row = round(data["pnl"] * qty / data["qty"])
            avg_cost = round(data["avg_buy_price"], 2)

            print(f"  [{sym}] row={row_info['row']} qty={qty} "
                  f"avg_cost={avg_cost} pnl={pnl_for_row}")
            updates.append({
                "row": row_info["row"],
                "avg_cost": avg_cost,
                "pnl": pnl_for_row,
            })

        if updates:
            batch_update_pnl(sheets_cfg, updates)

    print("[main] backfill 完成。")


def main():
    args = parse_args()

    if args.backfill_pnl:
        target_date = date.fromisoformat(args.backfill_pnl)
        print(f"[main] backfill-pnl 目標日期：{target_date}")
        sheets_cfg = SheetsConfig.from_env()
        stock_names.load(
            sheets_cfg.service_account_json,
            sheets_cfg.sheet_id,
            sheets_cfg.stock_info_tab,
        )
        run_backfill_pnl(target_date, sheets_cfg)
        sys.exit(0)

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    print(f"[main] 目標日期：{target_date}")

    sheets_cfg = SheetsConfig.from_env()

    # 載入股票名稱對照表（來自 FuturesTrade 維護的「股票代號」tab）
    stock_names.load(
        sheets_cfg.service_account_json,
        sheets_cfg.sheet_id,
        sheets_cfg.stock_info_tab,
    )

    # 讀取各帳戶最後記錄日期，用於跳過已存在的資料
    print("[main] 讀取 Google Sheets 現有資料...")
    last_dates = get_last_dates(sheets_cfg)
    for account, last_date in last_dates.items():
        print(f"  {account}：最後記錄 {last_date}")

    brokers = get_enabled_brokers()
    all_rows: List[list] = []

    for broker in brokers:
        account = broker.account_name
        last_date = last_dates.get(account)

        # 跳過已記錄的日期
        if last_date is not None and target_date <= last_date:
            print(f"[{account}] {target_date} 已有記錄，跳過。")
            continue

        print(f"[{account}] 抓取 {target_date} 成交明細...")
        try:
            fills = broker.get_fills(target_date)
        except Exception as e:
            print(f"[{account}] 抓取失敗：{e}")
            continue

        if not fills:
            print(f"[{account}] 當日無成交。")
            continue

        print(f"[{account}] 取得 {len(fills)} 筆部分成交，合併中...")
        trades = merge_fills(fills)
        print(f"[{account}] 合併後 {len(trades)} 筆")

        for t in trades:
            print(f"  → {t.stock_name} {t.side} {t.quantity}股 @{t.price}")
            all_rows.append(t.to_row())

    if not all_rows:
        print("[main] 無新資料需要寫入。")
        sys.exit(0)

    print(f"[main] 共 {len(all_rows)} 列，寫入 Google Sheets...")
    append_rows(sheets_cfg, all_rows)
    print("[main] 完成。")


if __name__ == "__main__":
    main()
