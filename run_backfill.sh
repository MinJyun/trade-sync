#!/usr/bin/env bash
# 由 launchd 於週二~週六 00:10 呼叫，補填「前一天」交易的損益與平均成本。
# 元富/玉山的已實現損益都是過午夜才結算，故隔天 00:10 才補得到。
# 00:10 執行時 date -v-1d = 前一個日曆日，對應週一~週五的交易日。
set -uo pipefail
cd "$(dirname "$0")"

# watchdog：萬一 broker SDK 卡住（無 timeout），最多 5 分鐘就強制結束，
# 避免卡死的 job 佔住 launchd、擋掉之後每晚的排程。
/usr/bin/python3 main.py --backfill-pnl "$(date -v-1d +%Y-%m-%d)" &
PID=$!
( sleep 300; kill -9 "$PID" 2>/dev/null ) &
WATCHDOG=$!
wait "$PID" && STATUS=0 || STATUS=$?
kill "$WATCHDOG" 2>/dev/null
exit "$STATUS"
