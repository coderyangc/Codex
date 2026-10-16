#!/system/bin/sh

# Android 设备端独立运行入口。
# 不使用 adb，不要求电脑连接；在 Android 终端 App 中直接执行。
# 参数：时长 间隔 包名 输出CSV 配置文件（后两项可省略）

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" 2>/dev/null && pwd)
COLLECTOR="$SCRIPT_DIR/android_app_perf_monitor.sh"
DURATION=${1:-10m}
INTERVAL=${2:-10}
PACKAGES=${3:-com.uusafe.portal}
OUTPUT=${4:-"$SCRIPT_DIR/portal_$(date +%Y%m%d_%H%M%S).csv"}
CONFIG=${5:-}
LOG="${OUTPUT%.*}.log"

die() {
  echo "错误：$*" >&2
  exit 2
}

[ -r "$COLLECTOR" ] || die "找不到采集器：$COLLECTOR"

case "$OUTPUT" in
  */*) output_dir=${OUTPUT%/*}; [ "$output_dir" = "$OUTPUT" ] && output_dir=".";;
  *) output_dir=".";;
esac
mkdir -p "$output_dir" 2>/dev/null || die "无法创建输出目录：$output_dir"
touch "$OUTPUT" 2>/dev/null || die "输出文件不可写：$OUTPUT"
touch "$LOG" 2>/dev/null || die "日志文件不可写：$LOG"

echo "Android 设备本地监控"
echo "包名：$PACKAGES"
echo "时长：$DURATION；间隔：$INTERVAL"
echo "CSV：$OUTPUT"
echo "日志：$LOG"
echo "开始采集；可按 Ctrl+C 停止，已写入的数据会保留。"

if [ -n "$CONFIG" ]; then
  [ -r "$CONFIG" ] || die "配置文件不可读：$CONFIG"
  BASE_ROOT="${BASE_ROOT:-${TMPDIR:-.}}" sh "$COLLECTOR" "$DURATION" "$INTERVAL" "$PACKAGES" none "$CONFIG" > "$OUTPUT" 2> "$LOG"
else
  BASE_ROOT="${BASE_ROOT:-${TMPDIR:-.}}" sh "$COLLECTOR" "$DURATION" "$INTERVAL" "$PACKAGES" none > "$OUTPUT" 2> "$LOG"
fi

code=$?
if [ "$code" -eq 0 ]; then
  echo "采集完成：$OUTPUT"
else
  echo "采集结束但返回码为 $code；请检查日志：$LOG" >&2
fi
exit "$code"
