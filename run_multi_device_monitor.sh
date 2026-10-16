#!/usr/bin/env bash

# macOS 多设备 Android 应用性能监控入口。
# 采集器在 Android 设备端执行；Mac 端按 serial 并行启动并保存 CSV。

set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ADB=${ADB:-adb}
COLLECTOR="$SCRIPT_DIR/android_app_perf_monitor.sh"
DURATION="10m"
INTERVAL="10"
PACKAGES="com.uusafe.portal"
LAUNCH_MODE="none"
OUTPUT_DIR="$SCRIPT_DIR/reports"
CONFIG=""
SERIAL_SPEC=""
DRY_RUN=0
LIST_ONLY=0

usage() {
  cat <<'EOF'
用法：
  ./run_multi_device_monitor.sh [选项]

默认行为：发现所有状态为 device 的 ADB 设备，并行监控 10 分钟，每 10 秒采样一次。

选项：
  --duration VALUE     监控时长，例如 30s、10m、2h；默认 10m
  --interval VALUE     采样间隔，例如 5、10s；默认 10
  --packages VALUE     包名，多个包名用英文逗号分隔；默认 com.uusafe.portal
  --serial VALUE       只监控指定设备；多个 serial 用英文逗号分隔
  --output-dir PATH    CSV 和日志保存目录；默认 ./reports
  --config PATH        使用采集配置文件；默认使用采集器内置默认配置
  --launch MODE        none、cold 或 hot；默认 none
  --list               只列出当前在线设备，不开始采集
  --dry-run            显示将要执行的命令，不开始采集
  -h, --help           显示帮助

示例：
  ./run_multi_device_monitor.sh
  ./run_multi_device_monitor.sh --duration 30m --interval 5
  ./run_multi_device_monitor.sh --serial ABC123,192.168.1.20:5555
  ./run_multi_device_monitor.sh --output-dir ./reports --config ./采集配置.conf
EOF
}

die() {
  echo "错误：$*" >&2
  exit 2
}

require_value() {
  [ "$#" -ge 2 ] || die "参数缺少值：$1"
  [ -n "$2" ] || die "参数缺少值：$1"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --duration)
      require_value "$@"
      DURATION=$2
      shift 2
      ;;
    --interval)
      require_value "$@"
      INTERVAL=$2
      shift 2
      ;;
    --packages|--package)
      require_value "$@"
      PACKAGES=$2
      shift 2
      ;;
    --serial)
      require_value "$@"
      SERIAL_SPEC=$2
      shift 2
      ;;
    --output-dir)
      require_value "$@"
      OUTPUT_DIR=$2
      shift 2
      ;;
    --config)
      require_value "$@"
      CONFIG=$2
      shift 2
      ;;
    --launch)
      require_value "$@"
      LAUNCH_MODE=$2
      shift 2
      ;;
    --list)
      LIST_ONLY=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知参数：$1；使用 --help 查看用法"
      ;;
  esac
done

command -v "$ADB" >/dev/null 2>&1 || die "找不到 adb。请先安装 Android Platform-Tools，或设置 ADB=/path/to/adb"
[ -r "$COLLECTOR" ] || die "找不到采集器：$COLLECTOR"
[ -z "$CONFIG" ] || [ -r "$CONFIG" ] || die "配置文件不可读：$CONFIG"
case "$LAUNCH_MODE" in
  none|cold|hot) ;;
  *) die "--launch 只能是 none、cold 或 hot" ;;
esac

if ! "$ADB" start-server >/dev/null 2>&1; then
  die "ADB server 启动失败"
fi

list_adb_devices() {
  "$ADB" devices | awk '$2 == "device" {print $1}'
}

print_devices() {
  echo "当前在线设备："
  "$ADB" devices -l
}

if [ "$LIST_ONLY" -eq 1 ]; then
  print_devices
  exit 0
fi

if [ -n "$SERIAL_SPEC" ]; then
  IFS=',' read -r -a requested_serials <<< "$SERIAL_SPEC"
  serials=()
  for serial in "${requested_serials[@]}"; do
    serial=$(printf '%s' "$serial" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -n "$serial" ] || continue
    state=$("$ADB" -s "$serial" get-state 2>/dev/null || true)
    [ "$state" = "device" ] || die "设备不可用：$serial（当前状态：${state:-未连接}）"
    serials+=("$serial")
  done
else
  serials=()
  while IFS= read -r serial; do
    [ -n "$serial" ] && serials+=("$serial")
  done < <(list_adb_devices)
fi

[ "${#serials[@]}" -gt 0 ] || die "没有状态为 device 的 ADB 设备"

mkdir -p "$OUTPUT_DIR" || die "无法创建输出目录：$OUTPUT_DIR"

stamp=$(date +%Y%m%d_%H%M%S)
pids=()
pid_serials=()
remote_configs=()
remote_config_serials=()

cleanup_remote_configs() {
  i=0
  while [ "$i" -lt "${#remote_configs[@]}" ]; do
    serial=${remote_config_serials[$i]}
    remote=${remote_configs[$i]}
    "$ADB" -s "$serial" shell rm -f "$remote" >/dev/null 2>&1 || true
    i=$((i + 1))
  done
}

stop_children() {
  for pid in "${pids[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
}

on_interrupt() {
  echo
  echo "收到中断信号，停止仍在运行的设备采集；已写入的 CSV 将保留。" >&2
  stop_children
  wait >/dev/null 2>&1 || true
  cleanup_remote_configs
  exit 130
}

trap on_interrupt INT TERM
trap cleanup_remote_configs EXIT

echo "将监控 ${#serials[@]} 台设备：时长=${DURATION}，间隔=${INTERVAL}，包名=${PACKAGES}"

i=0
for serial in "${serials[@]}"; do
  safe_serial=$(printf '%s' "$serial" | tr '/: ' '___')
  csv="$OUTPUT_DIR/portal_${safe_serial}_${stamp}.csv"
  log="$OUTPUT_DIR/portal_${safe_serial}_${stamp}.log"
  remote_config="/data/local/tmp/android_app_perf_monitor_config_${$}_${i}.conf"

  if [ -n "$CONFIG" ]; then
    config_arg="$remote_config"
    if [ "$DRY_RUN" -eq 0 ]; then
      if ! "$ADB" -s "$serial" push "$CONFIG" "$remote_config" >/dev/null 2>"$log"; then
        echo "[$serial] 配置文件推送失败，跳过；详见 $log" >&2
        i=$((i + 1))
        continue
      fi
      remote_configs+=("$remote_config")
      remote_config_serials+=("$serial")
    fi
  else
    config_arg=""
  fi

  if [ "$CONFIG" = "" ]; then
    cmd=("$ADB" -s "$serial" shell sh -s "$DURATION" "$INTERVAL" "$PACKAGES" "$LAUNCH_MODE")
  else
    cmd=("$ADB" -s "$serial" shell sh -s "$DURATION" "$INTERVAL" "$PACKAGES" "$LAUNCH_MODE" "$config_arg")
  fi

  echo "[$serial] CSV: $csv"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[%s] 命令：' "$serial"
    printf '%q ' "${cmd[@]}"
    printf '< %q > %q 2> %q\n' "$COLLECTOR" "$csv" "$log"
    i=$((i + 1))
    continue
  fi

  "${cmd[@]}" < "$COLLECTOR" > "$csv" 2>> "$log" &
  pids+=("$!")
  pid_serials+=("$serial")
  i=$((i + 1))
done

if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run 完成，未开始采集。"
  exit 0
fi

[ "${#pids[@]}" -gt 0 ] || die "没有成功启动任何设备采集"

failed=0
i=0
while [ "$i" -lt "${#pids[@]}" ]; do
  if wait "${pids[$i]}"; then
    echo "[${pid_serials[$i]}] 采集完成"
  else
    echo "[${pid_serials[$i]}] 采集失败；CSV 和日志仍保留" >&2
    failed=1
  fi
  i=$((i + 1))
done

echo
echo "采集任务结束，输出目录：$OUTPUT_DIR"
if [ "$failed" -ne 0 ]; then
  echo "部分设备失败，请检查对应 .log 文件。" >&2
  exit 1
fi
exit 0
