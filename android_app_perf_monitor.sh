#!/system/bin/sh
# Android app performance collector. One CSV row per package per sample.
# $1 duration, $2 interval, $3 comma-separated packages, $4 launch mode: none/cold/hot, $5 device-side config path.
TOOL_VERSION=2.0.9
# 兼容旧版 BAT/GUI 曾经插入的脚本名占位参数。
case "${1:-}" in
  android_app_perf_monitor|android_app_perf_monitor.sh) shift;;
esac
DURATION_SPEC=${1:-10m}
INTERVAL_SPEC=${2:-5}
PACKAGE_SPEC=${3:-com.uusafe.portal}
LAUNCH_MODE=${4:-none}
CONFIG_SPEC=${5:-}
CSV_HEADER_MODE=zh-en

csv_quote() { printf '"%s"' "$(printf '%s' "$1" | sed 's/"/""/g')"; }
emit_csv_row() {
  first=1
  for value in "$@"; do
    if [ "$first" -eq 1 ]; then first=0; else printf ','; fi
    printf '%s' "$value"
  done
  printf '\n'
}
to_seconds() {
  value=$1
  case "$value" in
    *h) n=${value%h}; case "$n" in ''|*[!0-9]*) return 1;; esac; echo $((n*3600));;
    *m) n=${value%m}; case "$n" in ''|*[!0-9]*) return 1;; esac; echo $((n*60));;
    *s) n=${value%s}; case "$n" in ''|*[!0-9]*) return 1;; esac; echo "$n";;
    ''|*[!0-9]*) return 1;;
    *) echo "$value";;
  esac
}
getprop_value() { getprop "$1" 2>/dev/null | tr -d '\r\n'; }
meminfo_value() {
  awk -F: -v wanted="$1" '$1 ~ wanted {v=$2; gsub(/[^0-9]/," ",v); n=split(v,a," "); for(i=1;i<=n;i++) if(a[i]!=""){print a[i]; exit}}' "$MEM_FILE"
}
kb_to_mb() {
  value=$1
  case "$value" in ''|*[!0-9.]*) return 0;; esac
  awk -v value="$value" 'BEGIN{printf "%.2f",value/1024}'
}
meminfo_mb() {
  value=$(meminfo_value "$1")
  [ -n "$value" ] && kb_to_mb "$value"
}
battery_value() { dumpsys battery 2>/dev/null | sed -n "s/^[[:space:]]*$1:[[:space:]]*//p" | head -n 1 | tr -d '\r'; }
thermal_max_c() {
  max=0
  for zone in /sys/class/thermal/thermal_zone*; do
    [ -r "$zone/temp" ] || continue
    v=$(cat "$zone/temp" 2>/dev/null)
    case "$v" in ''|*[!0-9-]*) continue;; esac
    [ "$v" -gt "$max" ] && max=$v
  done
  [ "$max" -gt 0 ] && awk -v v="$max" 'BEGIN{printf "%.2f",v/1000}'
}
net_bytes() {
  uid=$1
  [ -n "$uid" ] && [ -r /proc/net/xt_qtaguid/stats ] || return 0
  awk -v u="$uid" 'NR>1&&$4==u{r+=$6;t+=$8;ok=1}END{if(ok)print r "\t" t}' /proc/net/xt_qtaguid/stats
}
log_count() {
  logcat -d -b events -b system 2>/dev/null | grep -i "$1" | grep -Ei "$2" | wc -l | tr -d ' '
}
resumed_package() {
  dumpsys activity activities 2>/dev/null | awk '/mResumedActivity/{for(i=1;i<=NF;i++)if(index($i,"/")>0){sub(/\/.*/,"",$i);print $i;exit}}'
}

toggle_value() {
  value=$(printf '%s' "$1" | tr -d '\r' | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  case "$value" in
    1|true|yes|on) echo 1;;
    0|false|no|off) echo 0;;
    *) echo "$2";;
  esac
}

# 默认只检测 CPU 和内存；配置文件可以逐项开启其他项目。
COLLECT_PROCESS=0
COLLECT_CPU=1
COLLECT_MEMORY=1
COLLECT_DISK_IO=0
COLLECT_NETWORK=0
COLLECT_BATTERY=0
COLLECT_THERMAL=0
COLLECT_UI=0
COLLECT_STORAGE=0
COLLECT_STARTUP=0
COLLECT_STABILITY=0
COLLECT_SYSTEM=0
COLLECT_DEVICE=0

# 默认 CSV 只显示 CPU 和内存项目；配置文件可以逐项显示其他列。
DISPLAY_PROCESS=0
DISPLAY_PROCESS_COUNT=1
DISPLAY_PROCESS_PIDS=1
DISPLAY_PROCESS_MAIN_PID=1
DISPLAY_PROCESS_THREAD_COUNT=1
DISPLAY_CPU=1
DISPLAY_CPU_PCT_ALL_CORES=1
DISPLAY_CPU_PCT_ONE_CORE=1
DISPLAY_MAIN_THREAD_CPU_PCT=1
DISPLAY_MEMORY=1
DISPLAY_MEMORY_RSS=1
DISPLAY_MEMORY_PSS=1
DISPLAY_MEMORY_JAVA_HEAP=0
DISPLAY_MEMORY_NATIVE_HEAP=0
DISPLAY_MEMORY_CODE=0
DISPLAY_MEMORY_GRAPHICS=0
DISPLAY_MEMORY_STACK=0
DISPLAY_DISK_IO=0
DISPLAY_DISK_READ_BPS=1
DISPLAY_DISK_WRITE_BPS=1
DISPLAY_IO_READ_BYTES=1
DISPLAY_IO_WRITE_BYTES=1
DISPLAY_NETWORK=0
DISPLAY_NETWORK_UID=1
DISPLAY_NETWORK_RX_BPS=1
DISPLAY_NETWORK_TX_BPS=1
DISPLAY_NETWORK_RX_BYTES=1
DISPLAY_NETWORK_TX_BYTES=1
DISPLAY_NETWORK_TYPE=1
DISPLAY_BATTERY=0
DISPLAY_BATTERY_LEVEL_PCT=1
DISPLAY_BATTERY_STATUS=1
DISPLAY_BATTERY_TEMPERATURE_C=1
DISPLAY_BATTERY_VOLTAGE_MV=1
DISPLAY_BATTERY_CURRENT_UA=1
DISPLAY_APP_POWER_MAH=1
DISPLAY_THERMAL=0
DISPLAY_MAX_THERMAL_TEMP_C=1
DISPLAY_THERMAL_STATUS=1
DISPLAY_UI=0
DISPLAY_FRAMES_TOTAL=1
DISPLAY_JANKY_FRAMES=1
DISPLAY_JANK_PCT=1
DISPLAY_STORAGE=0
DISPLAY_APP_DATA_KB=1
DISPLAY_APK_KB=1
DISPLAY_APP_STORAGE_KB=1
DISPLAY_STARTUP=0
DISPLAY_FOREGROUND=1
DISPLAY_FOREGROUND_TRANSITION=1
DISPLAY_FIRST_PROCESS_SEEN_S=1
DISPLAY_FIRST_FOREGROUND_SEEN_S=1
DISPLAY_STARTUP_OBSERVED_S=1
DISPLAY_STARTUP_STATUS=1
DISPLAY_STABILITY=0
DISPLAY_CRASH_COUNT=1
DISPLAY_ANR_COUNT=1
DISPLAY_LOGCAT_ERROR_COUNT=1
DISPLAY_SYSTEM=0
DISPLAY_SYSTEM_CPU_PCT=1
DISPLAY_SYSTEM_LOAD_1M=1
DISPLAY_SYSTEM_MEM_TOTAL_MB=1
DISPLAY_SYSTEM_MEM_AVAILABLE_MB=1
DISPLAY_SYSTEM_STORAGE_FREE_KB=1
DISPLAY_DEVICE=0
DISPLAY_DEVICE_SERIAL=1
DISPLAY_DEVICE_MANUFACTURER=1
DISPLAY_DEVICE_BRAND=1
DISPLAY_DEVICE_MODEL=1
DISPLAY_ANDROID_VERSION=1
DISPLAY_ANDROID_SDK=1
DISPLAY_BUILD_ID=1
DISPLAY_ADB_STATE=0
DISPLAY_STATUS=1

load_config() {
  [ -n "$CONFIG_SPEC" ] || return 0
  [ -r "$CONFIG_SPEC" ] || { echo "config file is not readable: $CONFIG_SPEC" >&2; exit 2; }
  while IFS='=' read -r key value; do
    key=$(printf '%s' "$key" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    case "$key" in
      COLLECT_PROCESS) COLLECT_PROCESS=$(toggle_value "$value" "$COLLECT_PROCESS");;
      COLLECT_CPU) COLLECT_CPU=$(toggle_value "$value" "$COLLECT_CPU");;
      COLLECT_MEMORY) COLLECT_MEMORY=$(toggle_value "$value" "$COLLECT_MEMORY");;
      COLLECT_DISK_IO) COLLECT_DISK_IO=$(toggle_value "$value" "$COLLECT_DISK_IO");;
      COLLECT_NETWORK) COLLECT_NETWORK=$(toggle_value "$value" "$COLLECT_NETWORK");;
      COLLECT_BATTERY) COLLECT_BATTERY=$(toggle_value "$value" "$COLLECT_BATTERY");;
      COLLECT_THERMAL) COLLECT_THERMAL=$(toggle_value "$value" "$COLLECT_THERMAL");;
      COLLECT_UI) COLLECT_UI=$(toggle_value "$value" "$COLLECT_UI");;
      COLLECT_STORAGE) COLLECT_STORAGE=$(toggle_value "$value" "$COLLECT_STORAGE");;
      COLLECT_STARTUP) COLLECT_STARTUP=$(toggle_value "$value" "$COLLECT_STARTUP");;
      COLLECT_STABILITY) COLLECT_STABILITY=$(toggle_value "$value" "$COLLECT_STABILITY");;
      COLLECT_SYSTEM) COLLECT_SYSTEM=$(toggle_value "$value" "$COLLECT_SYSTEM");;
      COLLECT_DEVICE) COLLECT_DEVICE=$(toggle_value "$value" "$COLLECT_DEVICE");;
      DISPLAY_PROCESS) DISPLAY_PROCESS=$(toggle_value "$value" "$DISPLAY_PROCESS");;
      DISPLAY_PROCESS_COUNT) DISPLAY_PROCESS_COUNT=$(toggle_value "$value" "$DISPLAY_PROCESS_COUNT");;
      DISPLAY_PROCESS_PIDS) DISPLAY_PROCESS_PIDS=$(toggle_value "$value" "$DISPLAY_PROCESS_PIDS");;
      DISPLAY_PROCESS_MAIN_PID) DISPLAY_PROCESS_MAIN_PID=$(toggle_value "$value" "$DISPLAY_PROCESS_MAIN_PID");;
      DISPLAY_PROCESS_THREAD_COUNT) DISPLAY_PROCESS_THREAD_COUNT=$(toggle_value "$value" "$DISPLAY_PROCESS_THREAD_COUNT");;
      DISPLAY_CPU) DISPLAY_CPU=$(toggle_value "$value" "$DISPLAY_CPU");;
      DISPLAY_CPU_PCT_ALL_CORES) DISPLAY_CPU_PCT_ALL_CORES=$(toggle_value "$value" "$DISPLAY_CPU_PCT_ALL_CORES");;
      DISPLAY_CPU_PCT_ONE_CORE) DISPLAY_CPU_PCT_ONE_CORE=$(toggle_value "$value" "$DISPLAY_CPU_PCT_ONE_CORE");;
      DISPLAY_MAIN_THREAD_CPU_PCT) DISPLAY_MAIN_THREAD_CPU_PCT=$(toggle_value "$value" "$DISPLAY_MAIN_THREAD_CPU_PCT");;
      DISPLAY_MEMORY) DISPLAY_MEMORY=$(toggle_value "$value" "$DISPLAY_MEMORY");;
      DISPLAY_MEMORY_RSS) DISPLAY_MEMORY_RSS=$(toggle_value "$value" "$DISPLAY_MEMORY_RSS");;
      DISPLAY_MEMORY_PSS) DISPLAY_MEMORY_PSS=$(toggle_value "$value" "$DISPLAY_MEMORY_PSS");;
      DISPLAY_MEMORY_JAVA_HEAP) DISPLAY_MEMORY_JAVA_HEAP=$(toggle_value "$value" "$DISPLAY_MEMORY_JAVA_HEAP");;
      DISPLAY_MEMORY_NATIVE_HEAP) DISPLAY_MEMORY_NATIVE_HEAP=$(toggle_value "$value" "$DISPLAY_MEMORY_NATIVE_HEAP");;
      DISPLAY_MEMORY_CODE) DISPLAY_MEMORY_CODE=$(toggle_value "$value" "$DISPLAY_MEMORY_CODE");;
      DISPLAY_MEMORY_GRAPHICS) DISPLAY_MEMORY_GRAPHICS=$(toggle_value "$value" "$DISPLAY_MEMORY_GRAPHICS");;
      DISPLAY_MEMORY_STACK) DISPLAY_MEMORY_STACK=$(toggle_value "$value" "$DISPLAY_MEMORY_STACK");;
      DISPLAY_DISK_IO) DISPLAY_DISK_IO=$(toggle_value "$value" "$DISPLAY_DISK_IO");;
      DISPLAY_DISK_READ_BPS) DISPLAY_DISK_READ_BPS=$(toggle_value "$value" "$DISPLAY_DISK_READ_BPS");;
      DISPLAY_DISK_WRITE_BPS) DISPLAY_DISK_WRITE_BPS=$(toggle_value "$value" "$DISPLAY_DISK_WRITE_BPS");;
      DISPLAY_IO_READ_BYTES) DISPLAY_IO_READ_BYTES=$(toggle_value "$value" "$DISPLAY_IO_READ_BYTES");;
      DISPLAY_IO_WRITE_BYTES) DISPLAY_IO_WRITE_BYTES=$(toggle_value "$value" "$DISPLAY_IO_WRITE_BYTES");;
      DISPLAY_NETWORK) DISPLAY_NETWORK=$(toggle_value "$value" "$DISPLAY_NETWORK");;
      DISPLAY_NETWORK_UID) DISPLAY_NETWORK_UID=$(toggle_value "$value" "$DISPLAY_NETWORK_UID");;
      DISPLAY_NETWORK_RX_BPS) DISPLAY_NETWORK_RX_BPS=$(toggle_value "$value" "$DISPLAY_NETWORK_RX_BPS");;
      DISPLAY_NETWORK_TX_BPS) DISPLAY_NETWORK_TX_BPS=$(toggle_value "$value" "$DISPLAY_NETWORK_TX_BPS");;
      DISPLAY_NETWORK_RX_BYTES) DISPLAY_NETWORK_RX_BYTES=$(toggle_value "$value" "$DISPLAY_NETWORK_RX_BYTES");;
      DISPLAY_NETWORK_TX_BYTES) DISPLAY_NETWORK_TX_BYTES=$(toggle_value "$value" "$DISPLAY_NETWORK_TX_BYTES");;
      DISPLAY_NETWORK_TYPE) DISPLAY_NETWORK_TYPE=$(toggle_value "$value" "$DISPLAY_NETWORK_TYPE");;
      DISPLAY_BATTERY) DISPLAY_BATTERY=$(toggle_value "$value" "$DISPLAY_BATTERY");;
      DISPLAY_BATTERY_LEVEL_PCT) DISPLAY_BATTERY_LEVEL_PCT=$(toggle_value "$value" "$DISPLAY_BATTERY_LEVEL_PCT");;
      DISPLAY_BATTERY_STATUS) DISPLAY_BATTERY_STATUS=$(toggle_value "$value" "$DISPLAY_BATTERY_STATUS");;
      DISPLAY_BATTERY_TEMPERATURE_C) DISPLAY_BATTERY_TEMPERATURE_C=$(toggle_value "$value" "$DISPLAY_BATTERY_TEMPERATURE_C");;
      DISPLAY_BATTERY_VOLTAGE_MV) DISPLAY_BATTERY_VOLTAGE_MV=$(toggle_value "$value" "$DISPLAY_BATTERY_VOLTAGE_MV");;
      DISPLAY_BATTERY_CURRENT_UA) DISPLAY_BATTERY_CURRENT_UA=$(toggle_value "$value" "$DISPLAY_BATTERY_CURRENT_UA");;
      DISPLAY_APP_POWER_MAH) DISPLAY_APP_POWER_MAH=$(toggle_value "$value" "$DISPLAY_APP_POWER_MAH");;
      DISPLAY_THERMAL) DISPLAY_THERMAL=$(toggle_value "$value" "$DISPLAY_THERMAL");;
      DISPLAY_MAX_THERMAL_TEMP_C) DISPLAY_MAX_THERMAL_TEMP_C=$(toggle_value "$value" "$DISPLAY_MAX_THERMAL_TEMP_C");;
      DISPLAY_THERMAL_STATUS) DISPLAY_THERMAL_STATUS=$(toggle_value "$value" "$DISPLAY_THERMAL_STATUS");;
      DISPLAY_UI) DISPLAY_UI=$(toggle_value "$value" "$DISPLAY_UI");;
      DISPLAY_FRAMES_TOTAL) DISPLAY_FRAMES_TOTAL=$(toggle_value "$value" "$DISPLAY_FRAMES_TOTAL");;
      DISPLAY_JANKY_FRAMES) DISPLAY_JANKY_FRAMES=$(toggle_value "$value" "$DISPLAY_JANKY_FRAMES");;
      DISPLAY_JANK_PCT) DISPLAY_JANK_PCT=$(toggle_value "$value" "$DISPLAY_JANK_PCT");;
      DISPLAY_STORAGE) DISPLAY_STORAGE=$(toggle_value "$value" "$DISPLAY_STORAGE");;
      DISPLAY_APP_DATA_KB) DISPLAY_APP_DATA_KB=$(toggle_value "$value" "$DISPLAY_APP_DATA_KB");;
      DISPLAY_APK_KB) DISPLAY_APK_KB=$(toggle_value "$value" "$DISPLAY_APK_KB");;
      DISPLAY_APP_STORAGE_KB) DISPLAY_APP_STORAGE_KB=$(toggle_value "$value" "$DISPLAY_APP_STORAGE_KB");;
      DISPLAY_STARTUP) DISPLAY_STARTUP=$(toggle_value "$value" "$DISPLAY_STARTUP");;
      DISPLAY_FOREGROUND) DISPLAY_FOREGROUND=$(toggle_value "$value" "$DISPLAY_FOREGROUND");;
      DISPLAY_FOREGROUND_TRANSITION) DISPLAY_FOREGROUND_TRANSITION=$(toggle_value "$value" "$DISPLAY_FOREGROUND_TRANSITION");;
      DISPLAY_FIRST_PROCESS_SEEN_S) DISPLAY_FIRST_PROCESS_SEEN_S=$(toggle_value "$value" "$DISPLAY_FIRST_PROCESS_SEEN_S");;
      DISPLAY_FIRST_FOREGROUND_SEEN_S) DISPLAY_FIRST_FOREGROUND_SEEN_S=$(toggle_value "$value" "$DISPLAY_FIRST_FOREGROUND_SEEN_S");;
      DISPLAY_STARTUP_OBSERVED_S) DISPLAY_STARTUP_OBSERVED_S=$(toggle_value "$value" "$DISPLAY_STARTUP_OBSERVED_S");;
      DISPLAY_STARTUP_STATUS) DISPLAY_STARTUP_STATUS=$(toggle_value "$value" "$DISPLAY_STARTUP_STATUS");;
      DISPLAY_STABILITY) DISPLAY_STABILITY=$(toggle_value "$value" "$DISPLAY_STABILITY");;
      DISPLAY_CRASH_COUNT) DISPLAY_CRASH_COUNT=$(toggle_value "$value" "$DISPLAY_CRASH_COUNT");;
      DISPLAY_ANR_COUNT) DISPLAY_ANR_COUNT=$(toggle_value "$value" "$DISPLAY_ANR_COUNT");;
      DISPLAY_LOGCAT_ERROR_COUNT) DISPLAY_LOGCAT_ERROR_COUNT=$(toggle_value "$value" "$DISPLAY_LOGCAT_ERROR_COUNT");;
      DISPLAY_SYSTEM) DISPLAY_SYSTEM=$(toggle_value "$value" "$DISPLAY_SYSTEM");;
      DISPLAY_SYSTEM_CPU_PCT) DISPLAY_SYSTEM_CPU_PCT=$(toggle_value "$value" "$DISPLAY_SYSTEM_CPU_PCT");;
      DISPLAY_SYSTEM_LOAD_1M) DISPLAY_SYSTEM_LOAD_1M=$(toggle_value "$value" "$DISPLAY_SYSTEM_LOAD_1M");;
      DISPLAY_SYSTEM_MEM_TOTAL_MB) DISPLAY_SYSTEM_MEM_TOTAL_MB=$(toggle_value "$value" "$DISPLAY_SYSTEM_MEM_TOTAL_MB");;
      DISPLAY_SYSTEM_MEM_AVAILABLE_MB) DISPLAY_SYSTEM_MEM_AVAILABLE_MB=$(toggle_value "$value" "$DISPLAY_SYSTEM_MEM_AVAILABLE_MB");;
      DISPLAY_SYSTEM_STORAGE_FREE_KB) DISPLAY_SYSTEM_STORAGE_FREE_KB=$(toggle_value "$value" "$DISPLAY_SYSTEM_STORAGE_FREE_KB");;
      DISPLAY_DEVICE) DISPLAY_DEVICE=$(toggle_value "$value" "$DISPLAY_DEVICE");;
      DISPLAY_DEVICE_SERIAL) DISPLAY_DEVICE_SERIAL=$(toggle_value "$value" "$DISPLAY_DEVICE_SERIAL");;
      DISPLAY_DEVICE_MANUFACTURER) DISPLAY_DEVICE_MANUFACTURER=$(toggle_value "$value" "$DISPLAY_DEVICE_MANUFACTURER");;
      DISPLAY_DEVICE_BRAND) DISPLAY_DEVICE_BRAND=$(toggle_value "$value" "$DISPLAY_DEVICE_BRAND");;
      DISPLAY_DEVICE_MODEL) DISPLAY_DEVICE_MODEL=$(toggle_value "$value" "$DISPLAY_DEVICE_MODEL");;
      DISPLAY_ANDROID_VERSION) DISPLAY_ANDROID_VERSION=$(toggle_value "$value" "$DISPLAY_ANDROID_VERSION");;
      DISPLAY_ANDROID_SDK) DISPLAY_ANDROID_SDK=$(toggle_value "$value" "$DISPLAY_ANDROID_SDK");;
      DISPLAY_BUILD_ID) DISPLAY_BUILD_ID=$(toggle_value "$value" "$DISPLAY_BUILD_ID");;
      DISPLAY_ADB_STATE) DISPLAY_ADB_STATE=$(toggle_value "$value" "$DISPLAY_ADB_STATE");;
      DISPLAY_STATUS) DISPLAY_STATUS=$(toggle_value "$value" "$DISPLAY_STATUS");;
      CSV_HEADER_MODE) CSV_HEADER_MODE=$(printf '%s' "$value" | tr -d '\r' | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//');;
    esac
  done < "$CONFIG_SPEC"
}

load_config
case "$CSV_HEADER_MODE" in zh-en|en);; *) CSV_HEADER_MODE=zh-en;; esac
COLLECTION_FLAGS="process=$COLLECT_PROCESS;cpu=$COLLECT_CPU;memory=$COLLECT_MEMORY;disk_io=$COLLECT_DISK_IO;network=$COLLECT_NETWORK;battery=$COLLECT_BATTERY;thermal=$COLLECT_THERMAL;ui=$COLLECT_UI;storage=$COLLECT_STORAGE;startup=$COLLECT_STARTUP;stability=$COLLECT_STABILITY;system=$COLLECT_SYSTEM;device=$COLLECT_DEVICE"
DISPLAY_FLAGS="process=$DISPLAY_PROCESS;cpu=$DISPLAY_CPU;memory=$DISPLAY_MEMORY;disk_io=$DISPLAY_DISK_IO;network=$DISPLAY_NETWORK;battery=$DISPLAY_BATTERY;thermal=$DISPLAY_THERMAL;ui=$DISPLAY_UI;storage=$DISPLAY_STORAGE;startup=$DISPLAY_STARTUP;stability=$DISPLAY_STABILITY;system=$DISPLAY_SYSTEM;device=$DISPLAY_DEVICE"

DURATION=$(to_seconds "$DURATION_SPEC") || { echo "invalid duration: $DURATION_SPEC" >&2; exit 2; }
INTERVAL=$(to_seconds "$INTERVAL_SPEC") || { echo "invalid interval: $INTERVAL_SPEC" >&2; exit 2; }
case "$LAUNCH_MODE" in none|cold|hot);; *) echo "invalid launch mode: $LAUNCH_MODE" >&2; exit 2;; esac
[ "$DURATION" -gt 0 ] && [ "$INTERVAL" -gt 0 ] || { echo "duration and interval must be positive" >&2; exit 2; }

DEVICE_SERIAL=""; DEVICE_MANUFACTURER=""; DEVICE_BRAND=""; DEVICE_MODEL=""; ANDROID_VERSION=""; ANDROID_SDK=""; BUILD_ID=""
if [ "$COLLECT_DEVICE" -eq 1 ]; then
  DEVICE_SERIAL=$(getprop_value ro.serialno); [ -n "$DEVICE_SERIAL" ] || DEVICE_SERIAL=$(getprop_value ro.boot.serialno)
  DEVICE_MANUFACTURER=$(getprop_value ro.product.manufacturer)
  DEVICE_BRAND=$(getprop_value ro.product.brand)
  DEVICE_MODEL=$(getprop_value ro.product.model)
  ANDROID_VERSION=$(getprop_value ro.build.version.release)
  ANDROID_SDK=$(getprop_value ro.build.version.sdk)
  BUILD_ID=$(getprop_value ro.build.id)
fi
DEVICE_SERIAL_CSV=$(csv_quote "$DEVICE_SERIAL")
DEVICE_MANUFACTURER_CSV=$(csv_quote "$DEVICE_MANUFACTURER")
DEVICE_BRAND_CSV=$(csv_quote "$DEVICE_BRAND")
DEVICE_MODEL_CSV=$(csv_quote "$DEVICE_MODEL")
ANDROID_VERSION_CSV=$(csv_quote "$ANDROID_VERSION")
ANDROID_SDK_CSV=$(csv_quote "$ANDROID_SDK")
BUILD_ID_CSV=$(csv_quote "$BUILD_ID")

resolve_base_root() {
  # ADB shell 通常可以写 /data/local/tmp；普通 Android 终端 App 通常不能。
  # 直接在设备端运行时，优先选择终端 App 自己的临时目录或当前目录。
  if [ -n "${BASE_ROOT:-}" ]; then
    if mkdir -p "$BASE_ROOT" 2>/dev/null && [ -w "$BASE_ROOT" ]; then
      printf '%s\n' "$BASE_ROOT"
      return 0
    fi
  fi

  for candidate in "${TMPDIR:-}" "${PREFIX:-}/tmp" "${HOME:-}/.cache/android_app_perf_monitor" /data/local/tmp "."; do
    [ -n "$candidate" ] || continue
    if mkdir -p "$candidate" 2>/dev/null && [ -w "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

BASE_ROOT=$(resolve_base_root) || { echo "no writable temporary directory" >&2; exit 2; }
BASE="$BASE_ROOT/android_app_perf.$$"
PACKAGE_FILE="$BASE.packages"; PS_FILE="$BASE.ps"; MEM_FILE="$BASE.mem"; GFX_FILE="$BASE.gfx"
SIZE_FILE="$BASE.size"; IO_PREV="$BASE.io.prev"; IO_CURR="$BASE.io.curr"
CPU_PREV="$BASE.cpu.prev"; CPU_CURR="$BASE.cpu.curr"; STATE_FILE="$BASE.state"; EVENT_BASE="$BASE.events"
printf '%s\n' "$PACKAGE_SPEC" | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' > "$PACKAGE_FILE"
[ -s "$PACKAGE_FILE" ] || { echo "no package was provided" >&2; rm -f "$PACKAGE_FILE"; exit 2; }
while IFS= read -r p; do
  case "$p" in ''|*[!A-Za-z0-9._-]*) echo "invalid package name: $p" >&2; rm -f "$PACKAGE_FILE"; exit 2;; esac
done < "$PACKAGE_FILE"
cleanup() { rm -f "$PACKAGE_FILE" "$PS_FILE" "$MEM_FILE" "$GFX_FILE" "$SIZE_FILE" "$IO_PREV" "$IO_CURR" "$CPU_PREV" "$CPU_CURR" "$STATE_FILE" "$EVENT_BASE" "$STATE_FILE.tmp" "$CPU_PREV.tmp"; }
trap cleanup EXIT INT TERM

HEADER_EN="timestamp,elapsed_s,package"
HEADER_BI="时间戳（timestamp）,已运行时长（elapsed_s）,应用包名（package）"
append_header_group() {
  [ "$1" -eq 1 ] || return 0
  HEADER_EN="$HEADER_EN,$2"
  HEADER_BI="$HEADER_BI,$3"
}
append_group_field() {
  [ "$1" -eq 1 ] && [ "$2" -eq 1 ] || return 0
  HEADER_EN="$HEADER_EN,$3"
  HEADER_BI="$HEADER_BI,$4"
}
append_group_field "$DISPLAY_PROCESS" "$DISPLAY_PROCESS_COUNT" "process_count" "进程数量（process_count）"
append_group_field "$DISPLAY_PROCESS" "$DISPLAY_PROCESS_PIDS" "pids" "进程 PID（pids）"
append_group_field "$DISPLAY_PROCESS" "$DISPLAY_PROCESS_MAIN_PID" "main_pid" "主进程 PID（main_pid）"
append_group_field "$DISPLAY_PROCESS" "$DISPLAY_PROCESS_THREAD_COUNT" "thread_count" "线程数量（thread_count）"
append_group_field "$DISPLAY_CPU" "$DISPLAY_CPU_PCT_ALL_CORES" "cpu_pct_all_cores" "CPU 占用率（整机总算力）（cpu_pct_all_cores）"
append_group_field "$DISPLAY_CPU" "$DISPLAY_CPU_PCT_ONE_CORE" "cpu_pct_one_core" "CPU 占用率（单核标准）（cpu_pct_one_core）"
append_group_field "$DISPLAY_CPU" "$DISPLAY_MAIN_THREAD_CPU_PCT" "main_thread_cpu_pct" "主进程 CPU 占用率（字段名 main_thread_cpu_pct）"
append_group_field "$DISPLAY_MEMORY" "$DISPLAY_MEMORY_RSS" "rss_mb" "RSS 内存（MB）（rss_mb）"
append_group_field "$DISPLAY_MEMORY" "$DISPLAY_MEMORY_PSS" "pss_mb" "PSS 内存（MB）（pss_mb）"
append_group_field "$DISPLAY_MEMORY" "$DISPLAY_MEMORY_JAVA_HEAP" "java_heap_mb" "Java Heap（MB）（java_heap_mb）"
append_group_field "$DISPLAY_MEMORY" "$DISPLAY_MEMORY_NATIVE_HEAP" "native_heap_mb" "Native Heap（MB）（native_heap_mb）"
append_group_field "$DISPLAY_MEMORY" "$DISPLAY_MEMORY_CODE" "code_mb" "Code 内存（MB）（code_mb）"
append_group_field "$DISPLAY_MEMORY" "$DISPLAY_MEMORY_GRAPHICS" "graphics_mb" "Graphics 内存（MB）（graphics_mb）"
append_group_field "$DISPLAY_MEMORY" "$DISPLAY_MEMORY_STACK" "stack_mb" "Stack 内存（MB）（stack_mb）"
append_group_field "$DISPLAY_DISK_IO" "$DISPLAY_DISK_READ_BPS" "disk_read_Bps" "磁盘读取速率（disk_read_Bps）"
append_group_field "$DISPLAY_DISK_IO" "$DISPLAY_DISK_WRITE_BPS" "disk_write_Bps" "磁盘写入速率（disk_write_Bps）"
append_group_field "$DISPLAY_DISK_IO" "$DISPLAY_IO_READ_BYTES" "io_read_bytes" "累计磁盘读取量（io_read_bytes）"
append_group_field "$DISPLAY_DISK_IO" "$DISPLAY_IO_WRITE_BYTES" "io_write_bytes" "累计磁盘写入量（io_write_bytes）"
append_group_field "$DISPLAY_NETWORK" "$DISPLAY_NETWORK_UID" "network_uid" "应用 UID（network_uid）"
append_group_field "$DISPLAY_NETWORK" "$DISPLAY_NETWORK_RX_BPS" "network_rx_Bps" "网络接收速率（network_rx_Bps）"
append_group_field "$DISPLAY_NETWORK" "$DISPLAY_NETWORK_TX_BPS" "network_tx_Bps" "网络发送速率（network_tx_Bps）"
append_group_field "$DISPLAY_NETWORK" "$DISPLAY_NETWORK_RX_BYTES" "network_rx_bytes" "累计网络接收量（network_rx_bytes）"
append_group_field "$DISPLAY_NETWORK" "$DISPLAY_NETWORK_TX_BYTES" "network_tx_bytes" "累计网络发送量（network_tx_bytes）"
append_group_field "$DISPLAY_NETWORK" "$DISPLAY_NETWORK_TYPE" "network_type" "网络类型（network_type）"
append_group_field "$DISPLAY_BATTERY" "$DISPLAY_BATTERY_LEVEL_PCT" "battery_level_pct" "电池电量（battery_level_pct）"
append_group_field "$DISPLAY_BATTERY" "$DISPLAY_BATTERY_STATUS" "battery_status" "充电状态（battery_status）"
append_group_field "$DISPLAY_BATTERY" "$DISPLAY_BATTERY_TEMPERATURE_C" "battery_temperature_c" "电池温度（battery_temperature_c）"
append_group_field "$DISPLAY_BATTERY" "$DISPLAY_BATTERY_VOLTAGE_MV" "battery_voltage_mv" "电池电压（battery_voltage_mv）"
append_group_field "$DISPLAY_BATTERY" "$DISPLAY_BATTERY_CURRENT_UA" "battery_current_ua" "电池电流（battery_current_ua）"
append_group_field "$DISPLAY_BATTERY" "$DISPLAY_APP_POWER_MAH" "app_power_mah" "应用耗电量（app_power_mah）"
append_group_field "$DISPLAY_THERMAL" "$DISPLAY_MAX_THERMAL_TEMP_C" "max_thermal_temp_c" "最高热区温度（max_thermal_temp_c）"
append_group_field "$DISPLAY_THERMAL" "$DISPLAY_THERMAL_STATUS" "thermal_status" "热限制状态（thermal_status）"
append_group_field "$DISPLAY_UI" "$DISPLAY_FRAMES_TOTAL" "frames_total" "总渲染帧数（frames_total）"
append_group_field "$DISPLAY_UI" "$DISPLAY_JANKY_FRAMES" "janky_frames" "卡顿帧数（janky_frames）"
append_group_field "$DISPLAY_UI" "$DISPLAY_JANK_PCT" "jank_pct" "卡顿比例（jank_pct）"
append_group_field "$DISPLAY_STORAGE" "$DISPLAY_APP_DATA_KB" "app_data_kb" "应用数据占用（app_data_kb）"
append_group_field "$DISPLAY_STORAGE" "$DISPLAY_APK_KB" "apk_kb" "APK 占用（apk_kb）"
append_group_field "$DISPLAY_STORAGE" "$DISPLAY_APP_STORAGE_KB" "app_storage_kb" "应用总存储（app_storage_kb）"
append_group_field "$DISPLAY_STARTUP" "$DISPLAY_FOREGROUND" "foreground" "前后台状态（foreground）"
append_group_field "$DISPLAY_STARTUP" "$DISPLAY_FOREGROUND_TRANSITION" "foreground_transition" "前后台切换（foreground_transition）"
append_group_field "$DISPLAY_STARTUP" "$DISPLAY_FIRST_PROCESS_SEEN_S" "first_process_seen_s" "首次发现进程耗时（first_process_seen_s）"
append_group_field "$DISPLAY_STARTUP" "$DISPLAY_FIRST_FOREGROUND_SEEN_S" "first_foreground_seen_s" "首次进入前台耗时（first_foreground_seen_s）"
append_group_field "$DISPLAY_STARTUP" "$DISPLAY_STARTUP_OBSERVED_S" "startup_observed_s" "启动观测耗时（startup_observed_s）"
append_group_field "$DISPLAY_STARTUP" "$DISPLAY_STARTUP_STATUS" "startup_status" "启动测量状态（startup_status）"
append_group_field "$DISPLAY_STABILITY" "$DISPLAY_CRASH_COUNT" "crash_count" "崩溃次数（crash_count）"
append_group_field "$DISPLAY_STABILITY" "$DISPLAY_ANR_COUNT" "anr_count" "ANR 次数（anr_count）"
append_group_field "$DISPLAY_STABILITY" "$DISPLAY_LOGCAT_ERROR_COUNT" "logcat_error_count" "Logcat 错误数（logcat_error_count）"
append_group_field "$DISPLAY_SYSTEM" "$DISPLAY_SYSTEM_CPU_PCT" "system_cpu_pct" "整机 CPU 占用率（system_cpu_pct）"
append_group_field "$DISPLAY_SYSTEM" "$DISPLAY_SYSTEM_LOAD_1M" "system_load_1m" "系统 1 分钟负载（system_load_1m）"
append_group_field "$DISPLAY_SYSTEM" "$DISPLAY_SYSTEM_MEM_TOTAL_MB" "system_mem_total_mb" "整机总内存（MB）（system_mem_total_mb）"
append_group_field "$DISPLAY_SYSTEM" "$DISPLAY_SYSTEM_MEM_AVAILABLE_MB" "system_mem_available_mb" "整机可用内存（MB）（system_mem_available_mb）"
append_group_field "$DISPLAY_SYSTEM" "$DISPLAY_SYSTEM_STORAGE_FREE_KB" "system_storage_free_kb" "整机可用存储（system_storage_free_kb）"
append_group_field "$DISPLAY_DEVICE" "$DISPLAY_DEVICE_SERIAL" "device_serial" "设备序列号（device_serial）"
append_group_field "$DISPLAY_DEVICE" "$DISPLAY_DEVICE_MANUFACTURER" "device_manufacturer" "设备厂商（device_manufacturer）"
append_group_field "$DISPLAY_DEVICE" "$DISPLAY_DEVICE_BRAND" "device_brand" "设备品牌（device_brand）"
append_group_field "$DISPLAY_DEVICE" "$DISPLAY_DEVICE_MODEL" "device_model" "设备型号（device_model）"
append_group_field "$DISPLAY_DEVICE" "$DISPLAY_ANDROID_VERSION" "android_version" "Android 版本（android_version）"
append_group_field "$DISPLAY_DEVICE" "$DISPLAY_ANDROID_SDK" "android_sdk" "Android SDK（android_sdk）"
append_group_field "$DISPLAY_DEVICE" "$DISPLAY_BUILD_ID" "build_id" "系统 Build ID（build_id）"
append_header_group "$DISPLAY_ADB_STATE" "adb_state" "ADB 状态（adb_state）"
append_header_group "$DISPLAY_STATUS" "status" "采集状态（status）"
# 输出 UTF-8 BOM，确保 Windows Excel 直接打开中文 CSV 时按 UTF-8 解码。
printf '\357\273\277'
if [ "$CSV_HEADER_MODE" = en ]; then printf '%s\n' "$HEADER_EN"; else printf '%s\n' "$HEADER_BI"; fi

: > "$IO_PREV"; : > "$CPU_PREV"; : > "$STATE_FILE"
while IFS= read -r PACKAGE; do
  cb=0; ab=0
  if [ "$COLLECT_STABILITY" -eq 1 ]; then
    cb=$(log_count "$PACKAGE" 'crash|am_crash|fatal'); ab=$(log_count "$PACKAGE" 'anr|not responding|am_anr')
  fi
  case "$cb" in ''|*[!0-9]*) cb=0;; esac; case "$ab" in ''|*[!0-9]*) ab=0;; esac
  printf '%s\t%s\t%s\n' "$PACKAGE" "$cb" "$ab" >> "$EVENT_BASE"
done < "$PACKAGE_FILE"
if [ "$LAUNCH_MODE" != none ]; then
  while IFS= read -r PACKAGE; do
    [ "$LAUNCH_MODE" = cold ] && am force-stop "$PACKAGE" >/dev/null 2>&1
    monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
  done < "$PACKAGE_FILE"
fi

start=$(date +%s); last_sample=$start; next_storage=0; previous_total_ticks=""
while :; do
  now=$(date +%s); elapsed=$((now-start)); [ "$elapsed" -ge "$DURATION" ] && break
  timestamp=$(date '+%Y-%m-%dT%H:%M:%S%z')
  sample_interval=$((now-last_sample)); [ "$sample_interval" -gt 0 ] || sample_interval=$INTERVAL; last_sample=$now

  cpu_line=$(awk '/^cpu /{print $2,$3,$4,$5,$6,$7,$8,$9;exit}' /proc/stat 2>/dev/null)
  set -- $cpu_line
  u=${1:-0}; n=${2:-0}; s=${3:-0}; idle=${4:-0}; wait=${5:-0}; irq=${6:-0}; sirq=${7:-0}; steal=${8:-0}
  total_ticks=$((u+n+s+idle+wait+irq+sirq+steal)); idle_ticks=$((idle+wait))
  cpu_count=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null); case "$cpu_count" in ''|*[!0-9]*) cpu_count=1;; esac
  system_cpu_pct=""
  if [ "$COLLECT_SYSTEM" -eq 1 ] && [ -n "$previous_total_ticks" ] && [ "$total_ticks" -gt "$previous_total_ticks" ]; then
    old_idle=$(awk -F '\t' '$1=="__system__"{print $2;exit}' "$CPU_PREV")
    if [ -n "$old_idle" ] && [ "$idle_ticks" -ge "$old_idle" ]; then
      td=$((total_ticks-previous_total_ticks)); id=$((idle_ticks-old_idle))
      system_cpu_pct=$(awk -v t="$td" -v i="$id" 'BEGIN{printf "%.2f",100*(t-i)/t}')
    fi
  fi

  ps_lines=$(ps -A -o PID=,NAME= 2>/dev/null); [ -n "$ps_lines" ] || ps_lines=$(ps -A 2>/dev/null | awk 'NR>1{print $2,$NF}')
  printf '%s\n' "$ps_lines" > "$PS_FILE"
  : > "$IO_CURR"; : > "$CPU_CURR"; refresh_storage=0
  if [ "$now" -ge "$next_storage" ]; then refresh_storage=1; : > "$SIZE_FILE"; fi
  system_load_1m=""; system_mem_total_mb=""; system_mem_available_mb=""; system_storage_free_kb=""
  if [ "$COLLECT_SYSTEM" -eq 1 ]; then
    system_load_1m=$(awk '{print $1;exit}' /proc/loadavg 2>/dev/null)
    system_mem_total_mb=$(awk '$1=="MemTotal:"{printf "%.2f",$2/1024;exit}' /proc/meminfo 2>/dev/null)
    system_mem_available_mb=$(awk '$1=="MemAvailable:"{printf "%.2f",$2/1024;exit}' /proc/meminfo 2>/dev/null)
    system_storage_free_kb=$(df -k /data 2>/dev/null | awk 'NR>1{print $4;exit}')
  fi
  network_type=""
  if [ "$COLLECT_NETWORK" -eq 1 ]; then
    network_type=$(dumpsys connectivity 2>/dev/null | sed -n 's/.*NetworkAgentInfo.*type: \([^, ]*\).*/\1/p' | head -n 1); [ -n "$network_type" ] || network_type=unknown
  fi
  battery_level_pct=""; battery_status=""; battery_temperature_c=""; battery_voltage_mv=""; battery_current_ua=""; app_power_mah=""
  if [ "$COLLECT_BATTERY" -eq 1 ]; then
    battery_level_pct=$(battery_value level); battery_status=$(battery_value status)
    battery_voltage_mv=$(battery_value voltage); battery_current_ua=$(battery_value 'current now')
  fi
  max_thermal_temp_c=""; thermal_status=""
  if [ "$COLLECT_THERMAL" -eq 1 ]; then
    battery_temperature_raw=$(battery_value temperature)
    case "$battery_temperature_raw" in ''|*[!0-9-]*) battery_temperature_c="";; *) battery_temperature_c=$(awk -v t="$battery_temperature_raw" 'BEGIN{printf "%.2f",t/10}');; esac
    max_thermal_temp_c=$(thermal_max_c)
    thermal_status=$(dumpsys thermalservice 2>/dev/null | sed -n 's/.*Thermal Status[^0-9]*\([0-9]*\).*/\1/p' | head -n 1)
  fi

  while IFS= read -r PACKAGE; do
    pids=$(awk -v p="$PACKAGE" '$2==p||index($2,p":")==1{printf "%s|",$1}' "$PS_FILE"); pids=${pids%|}
    process_count=0; proc_ticks=0; main_pid=""; main_ticks=0; thread_count=0; rss_kb_total=0; rss_mb=""; rss_samples=0
    io_read=""; io_write=""; io_read_samples=0; io_write_samples=0
    for pid in $(printf '%s' "$pids" | tr '|' ' '); do
      process_count=$((process_count+1)); [ -n "$main_pid" ] || main_pid=$pid
      if [ "$COLLECT_CPU" -eq 1 ]; then
        stat_fields=$(awk '{print $14,$15}' /proc/"$pid"/stat 2>/dev/null); set -- $stat_fields
        case "$1" in ''|*[!0-9]*) ;; *) proc_ticks=$((proc_ticks+$1));; esac
        case "$2" in ''|*[!0-9]*) ;; *) proc_ticks=$((proc_ticks+$1));; esac
        if [ "$pid" = "$main_pid" ]; then
          case "$1" in ''|*[!0-9]*) ;; *) main_ticks=$((main_ticks+$1));; esac
          case "$2" in ''|*[!0-9]*) ;; *) main_ticks=$((main_ticks+$1));; esac
        fi
      fi
      if [ "$pid" = "$main_pid" ] && [ "$COLLECT_PROCESS" -eq 1 ]; then
        thread_count=$(ls /proc/"$pid"/task 2>/dev/null | wc -l | tr -d ' ')
      fi
      if [ "$COLLECT_MEMORY" -eq 1 ]; then
        one_rss=$(awk '$1=="VmRSS:"{print $2;exit}' /proc/"$pid"/status 2>/dev/null)
        case "$one_rss" in ''|*[!0-9]*) ;; *) rss_kb_total=$((rss_kb_total+one_rss)); rss_samples=$((rss_samples+1));; esac
      fi
      if [ "$COLLECT_DISK_IO" -eq 1 ]; then
        one_read=$(awk '$1=="read_bytes:"{print $2;exit}' /proc/"$pid"/io 2>/dev/null); one_write=$(awk '$1=="write_bytes:"{print $2;exit}' /proc/"$pid"/io 2>/dev/null)
        case "$one_read" in ''|*[!0-9]*) ;; *) [ -n "$io_read" ] && io_read=$((io_read+one_read)) || io_read=$one_read; io_read_samples=$((io_read_samples+1));; esac
        case "$one_write" in ''|*[!0-9]*) ;; *) [ -n "$io_write" ] && io_write=$((io_write+one_write)) || io_write=$one_write; io_write_samples=$((io_write_samples+1));; esac
      fi
    done
    [ "$rss_samples" -gt 0 ] && rss_mb=$(kb_to_mb "$rss_kb_total")

    cpu_pct_all=""; cpu_pct_one=""; main_thread_cpu_pct=""
    if [ "$COLLECT_CPU" -eq 1 ]; then
      old_proc_ticks=$(awk -F '\t' -v p="$PACKAGE" '$1==p{print $2;exit}' "$CPU_PREV")
      old_main_ticks=$(awk -F '\t' -v p="$PACKAGE" '$1==p{print $3;exit}' "$CPU_PREV")
      if [ -n "$previous_total_ticks" ] && [ "$total_ticks" -gt "$previous_total_ticks" ] && [ -n "$old_proc_ticks" ] && [ "$proc_ticks" -ge "$old_proc_ticks" ]; then
        td=$((total_ticks-previous_total_ticks)); pd=$((proc_ticks-old_proc_ticks))
        cpu_pct_all=$(awk -v p="$pd" -v t="$td" 'BEGIN{printf "%.2f",100*p/t}')
        cpu_pct_one=$(awk -v p="$cpu_pct_all" -v c="$cpu_count" 'BEGIN{printf "%.2f",p*c}')
        if [ -n "$old_main_ticks" ] && [ "$main_ticks" -ge "$old_main_ticks" ]; then
          md=$((main_ticks-old_main_ticks)); main_thread_cpu_pct=$(awk -v p="$md" -v t="$td" -v c="$cpu_count" 'BEGIN{printf "%.2f",100*p/t*c}')
        fi
      fi
      printf '%s\t%s\t%s\n' "$PACKAGE" "$proc_ticks" "$main_ticks" >> "$CPU_CURR"
    fi

    pss_mb=""; java_heap_mb=""; native_heap_mb=""; code_mb=""; graphics_mb=""; stack_mb=""
    if [ "$COLLECT_MEMORY" -eq 1 ]; then
      dumpsys meminfo "$PACKAGE" 2>/dev/null > "$MEM_FILE"
      pss_mb=$(awk '$1=="TOTAL"&&$2~/^[0-9]+$/{printf "%.2f",$2/1024;exit}' "$MEM_FILE")
      java_heap_mb=$(meminfo_mb 'Java Heap'); native_heap_mb=$(meminfo_mb 'Native Heap'); code_mb=$(meminfo_mb '^Code'); graphics_mb=$(meminfo_mb 'Graphics'); stack_mb=$(meminfo_mb 'Stack')
    else
      rss_mb=""
    fi
    read_bps=""; write_bps=""; old_read=$(awk -F '\t' -v p="$PACKAGE" '$1==p{print $2;exit}' "$IO_PREV"); old_write=$(awk -F '\t' -v p="$PACKAGE" '$1==p{print $3;exit}' "$IO_PREV")
    if [ "$COLLECT_DISK_IO" -eq 1 ]; then
      if [ -n "$io_read" ] && [ -n "$old_read" ] && [ "$io_read" -ge "$old_read" ]; then read_bps=$(awk -v d="$((io_read-old_read))" -v i="$sample_interval" 'BEGIN{printf "%.2f",d/i}'); fi
      if [ -n "$io_write" ] && [ -n "$old_write" ] && [ "$io_write" -ge "$old_write" ]; then write_bps=$(awk -v d="$((io_write-old_write))" -v i="$sample_interval" 'BEGIN{printf "%.2f",d/i}'); fi
    fi
    network_uid=""; network_rx_bytes=""; network_tx_bytes=""; network_rx_Bps=""; network_tx_Bps=""
    if [ "$COLLECT_NETWORK" -eq 1 ]; then
      network_uid=$(dumpsys package "$PACKAGE" 2>/dev/null | sed -n 's/.*userId=\([0-9]*\).*/\1/p' | head -n 1)
      network_values=$(net_bytes "$network_uid"); set -- $network_values; network_rx_bytes=${1:-}; network_tx_bytes=${2:-}
      old_network_rx=$(awk -F '\t' -v p="$PACKAGE" '$1==p{print $4;exit}' "$IO_PREV"); old_network_tx=$(awk -F '\t' -v p="$PACKAGE" '$1==p{print $5;exit}' "$IO_PREV")
      if [ -n "$network_rx_bytes" ] && [ -n "$old_network_rx" ] && [ "$network_rx_bytes" -ge "$old_network_rx" ]; then network_rx_Bps=$(awk -v d="$((network_rx_bytes-old_network_rx))" -v i="$sample_interval" 'BEGIN{printf "%.2f",d/i}'); fi
      if [ -n "$network_tx_bytes" ] && [ -n "$old_network_tx" ] && [ "$network_tx_bytes" -ge "$old_network_tx" ]; then network_tx_Bps=$(awk -v d="$((network_tx_bytes-old_network_tx))" -v i="$sample_interval" 'BEGIN{printf "%.2f",d/i}'); fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$PACKAGE" "$io_read" "$io_write" "$network_rx_bytes" "$network_tx_bytes" >> "$IO_CURR"

    if [ "$COLLECT_BATTERY" -eq 1 ]; then
      app_power_mah=$(dumpsys batterystats --charged "$PACKAGE" 2>/dev/null | sed -n 's/.*Total power:[[:space:]]*\([0-9.]*\)[[:space:]]*mAh.*/\1/p' | head -n 1)
    fi
    frames_total=""; janky_frames=""; jank_pct=""
    if [ "$COLLECT_UI" -eq 1 ]; then
      dumpsys gfxinfo "$PACKAGE" 2>/dev/null > "$GFX_FILE"
      frames_total=$(awk -F: '/Total frames rendered:/{gsub(/[^0-9]/,"",$2);print $2;exit}' "$GFX_FILE"); janky_frames=$(awk -F: '/Janky frames:/{print $2+0;exit}' "$GFX_FILE"); jank_pct=$(awk -F'[()]' '/Janky frames:/{gsub(/[^0-9.]/,"",$2);print $2;exit}' "$GFX_FILE")
    fi
    data_kb=""; apk_kb=""
    if [ "$COLLECT_STORAGE" -eq 1 ]; then
      if [ "$refresh_storage" -eq 1 ]; then
        data_dir=$(dumpsys package "$PACKAGE" 2>/dev/null | sed -n 's/.*dataDir=\([^, ]*\).*/\1/p' | head -n 1); candidate_data=$(du -sk "$data_dir" 2>/dev/null | awk 'NR==1{print $1}')
        case "$candidate_data" in ''|*[!0-9]*) ;; *) data_kb=$candidate_data;; esac
        apk_kb=$(for path in $(pm path "$PACKAGE" 2>/dev/null | sed 's/^package://'); do du -k "$path" 2>/dev/null; done | awk '{s+=$1}END{if(NR)print s}')
        case "$apk_kb" in ''|*[!0-9]*) apk_kb="";; esac
        printf '%s\t%s\t%s\n' "$PACKAGE" "$data_kb" "$apk_kb" >> "$SIZE_FILE"
      else
        data_kb=$(awk -F '\t' -v p="$PACKAGE" '$1==p{print $2;exit}' "$SIZE_FILE"); apk_kb=$(awk -F '\t' -v p="$PACKAGE" '$1==p{print $3;exit}' "$SIZE_FILE")
      fi
    fi
    app_storage_kb=""; [ -n "$data_kb" ] && [ -n "$apk_kb" ] && app_storage_kb=$((data_kb+apk_kb))

    foreground=""; transition=""; first_process=""; first_fg=""; startup_observed_s=""; startup_status=""
    if [ "$COLLECT_STARTUP" -eq 1 ]; then
      resumed=$(resumed_package); foreground=0; [ "$resumed" = "$PACKAGE" ] && foreground=1
      state=$(awk -F '\t' -v p="$PACKAGE" '$1==p{print;exit}' "$STATE_FILE"); first_process=$(printf '%s' "$state"|awk -F '\t' '{print $2}'); first_fg=$(printf '%s' "$state"|awk -F '\t' '{print $3}'); old_fg=$(printf '%s' "$state"|awk -F '\t' '{print $4}')
      [ -n "$first_process" ] || [ "$process_count" -le 0 ] || first_process=$elapsed
      [ -n "$first_fg" ] || [ "$foreground" -ne 1 ] || first_fg=$elapsed
      [ -n "$old_fg" ] && [ "$old_fg" != "$foreground" ] && { [ "$foreground" -eq 1 ] && transition=foreground || transition=background; }
      startup_status=not_launched
      if [ "$LAUNCH_MODE" != none ]; then startup_status=observed; startup_observed_s=$first_fg; [ -n "$startup_observed_s" ] || startup_status=not_observed; fi
      printf '%s\t%s\t%s\t%s\n' "$PACKAGE" "$first_process" "$first_fg" "$foreground" > "$STATE_FILE.tmp"; awk -F '\t' -v p="$PACKAGE" '$1!=p' "$STATE_FILE" >> "$STATE_FILE.tmp"; mv "$STATE_FILE.tmp" "$STATE_FILE"
    fi

    crash_count=""; anr_count=""; logcat_error_count=""
    if [ "$COLLECT_STABILITY" -eq 1 ]; then
      event=$(awk -F '\t' -v p="$PACKAGE" '$1==p{print;exit}' "$EVENT_BASE"); cb=$(printf '%s' "$event"|awk -F '\t' '{print $2}'); ab=$(printf '%s' "$event"|awk -F '\t' '{print $3}')
      crash_now=$(log_count "$PACKAGE" 'crash|am_crash|fatal'); anr_now=$(log_count "$PACKAGE" 'anr|not responding|am_anr'); logcat_error_count=$(log_count "$PACKAGE" 'error|exception|fatal|crash|anr|not responding')
      case "$crash_now" in ''|*[!0-9]*) crash_now=$cb;; esac; case "$anr_now" in ''|*[!0-9]*) anr_now=$ab;; esac; case "$logcat_error_count" in ''|*[!0-9]*) logcat_error_count="";; esac
      crash_count=$((crash_now-cb)); [ "$crash_count" -lt 0 ] && crash_count=0; anr_count=$((anr_now-ab)); [ "$anr_count" -lt 0 ] && anr_count=0
    fi

    status=ok; [ "$process_count" -gt 0 ] || status=no_process
    output_process_count=$process_count; output_pids=$pids; output_main_pid=$main_pid; output_thread_count=$thread_count
    [ "$COLLECT_PROCESS" -eq 1 ] || { output_process_count=""; output_pids=""; output_main_pid=""; output_thread_count=""; }
    # 采集/显示开关只从设备端配置文件读取，不写入 CSV 数据列。
    set -- "$timestamp" "$elapsed" "$PACKAGE"
    if [ "$DISPLAY_PROCESS" -eq 1 ]; then
      [ "$DISPLAY_PROCESS_COUNT" -eq 1 ] && set -- "$@" "$output_process_count"
      [ "$DISPLAY_PROCESS_PIDS" -eq 1 ] && set -- "$@" "$output_pids"
      [ "$DISPLAY_PROCESS_MAIN_PID" -eq 1 ] && set -- "$@" "$output_main_pid"
      [ "$DISPLAY_PROCESS_THREAD_COUNT" -eq 1 ] && set -- "$@" "$output_thread_count"
    fi
    if [ "$DISPLAY_CPU" -eq 1 ]; then
      [ "$DISPLAY_CPU_PCT_ALL_CORES" -eq 1 ] && set -- "$@" "$cpu_pct_all"
      [ "$DISPLAY_CPU_PCT_ONE_CORE" -eq 1 ] && set -- "$@" "$cpu_pct_one"
      [ "$DISPLAY_MAIN_THREAD_CPU_PCT" -eq 1 ] && set -- "$@" "$main_thread_cpu_pct"
    fi
    if [ "$DISPLAY_MEMORY" -eq 1 ]; then
      [ "$DISPLAY_MEMORY_RSS" -eq 1 ] && set -- "$@" "$rss_mb"
      [ "$DISPLAY_MEMORY_PSS" -eq 1 ] && set -- "$@" "$pss_mb"
      [ "$DISPLAY_MEMORY_JAVA_HEAP" -eq 1 ] && set -- "$@" "$java_heap_mb"
      [ "$DISPLAY_MEMORY_NATIVE_HEAP" -eq 1 ] && set -- "$@" "$native_heap_mb"
      [ "$DISPLAY_MEMORY_CODE" -eq 1 ] && set -- "$@" "$code_mb"
      [ "$DISPLAY_MEMORY_GRAPHICS" -eq 1 ] && set -- "$@" "$graphics_mb"
      [ "$DISPLAY_MEMORY_STACK" -eq 1 ] && set -- "$@" "$stack_mb"
    fi
    if [ "$DISPLAY_DISK_IO" -eq 1 ]; then
      [ "$DISPLAY_DISK_READ_BPS" -eq 1 ] && set -- "$@" "$read_bps"
      [ "$DISPLAY_DISK_WRITE_BPS" -eq 1 ] && set -- "$@" "$write_bps"
      [ "$DISPLAY_IO_READ_BYTES" -eq 1 ] && set -- "$@" "$io_read"
      [ "$DISPLAY_IO_WRITE_BYTES" -eq 1 ] && set -- "$@" "$io_write"
    fi
    if [ "$DISPLAY_NETWORK" -eq 1 ]; then
      [ "$DISPLAY_NETWORK_UID" -eq 1 ] && set -- "$@" "$network_uid"
      [ "$DISPLAY_NETWORK_RX_BPS" -eq 1 ] && set -- "$@" "$network_rx_Bps"
      [ "$DISPLAY_NETWORK_TX_BPS" -eq 1 ] && set -- "$@" "$network_tx_Bps"
      [ "$DISPLAY_NETWORK_RX_BYTES" -eq 1 ] && set -- "$@" "$network_rx_bytes"
      [ "$DISPLAY_NETWORK_TX_BYTES" -eq 1 ] && set -- "$@" "$network_tx_bytes"
      [ "$DISPLAY_NETWORK_TYPE" -eq 1 ] && set -- "$@" "$network_type"
    fi
    if [ "$DISPLAY_BATTERY" -eq 1 ]; then
      [ "$DISPLAY_BATTERY_LEVEL_PCT" -eq 1 ] && set -- "$@" "$battery_level_pct"
      [ "$DISPLAY_BATTERY_STATUS" -eq 1 ] && set -- "$@" "$battery_status"
      [ "$DISPLAY_BATTERY_TEMPERATURE_C" -eq 1 ] && set -- "$@" "$battery_temperature_c"
      [ "$DISPLAY_BATTERY_VOLTAGE_MV" -eq 1 ] && set -- "$@" "$battery_voltage_mv"
      [ "$DISPLAY_BATTERY_CURRENT_UA" -eq 1 ] && set -- "$@" "$battery_current_ua"
      [ "$DISPLAY_APP_POWER_MAH" -eq 1 ] && set -- "$@" "$app_power_mah"
    fi
    if [ "$DISPLAY_THERMAL" -eq 1 ]; then
      [ "$DISPLAY_MAX_THERMAL_TEMP_C" -eq 1 ] && set -- "$@" "$max_thermal_temp_c"
      [ "$DISPLAY_THERMAL_STATUS" -eq 1 ] && set -- "$@" "$thermal_status"
    fi
    if [ "$DISPLAY_UI" -eq 1 ]; then
      [ "$DISPLAY_FRAMES_TOTAL" -eq 1 ] && set -- "$@" "$frames_total"
      [ "$DISPLAY_JANKY_FRAMES" -eq 1 ] && set -- "$@" "$janky_frames"
      [ "$DISPLAY_JANK_PCT" -eq 1 ] && set -- "$@" "$jank_pct"
    fi
    if [ "$DISPLAY_STORAGE" -eq 1 ]; then
      [ "$DISPLAY_APP_DATA_KB" -eq 1 ] && set -- "$@" "$data_kb"
      [ "$DISPLAY_APK_KB" -eq 1 ] && set -- "$@" "$apk_kb"
      [ "$DISPLAY_APP_STORAGE_KB" -eq 1 ] && set -- "$@" "$app_storage_kb"
    fi
    if [ "$DISPLAY_STARTUP" -eq 1 ]; then
      [ "$DISPLAY_FOREGROUND" -eq 1 ] && set -- "$@" "$foreground"
      [ "$DISPLAY_FOREGROUND_TRANSITION" -eq 1 ] && set -- "$@" "$transition"
      [ "$DISPLAY_FIRST_PROCESS_SEEN_S" -eq 1 ] && set -- "$@" "$first_process"
      [ "$DISPLAY_FIRST_FOREGROUND_SEEN_S" -eq 1 ] && set -- "$@" "$first_fg"
      [ "$DISPLAY_STARTUP_OBSERVED_S" -eq 1 ] && set -- "$@" "$startup_observed_s"
      [ "$DISPLAY_STARTUP_STATUS" -eq 1 ] && set -- "$@" "$startup_status"
    fi
    if [ "$DISPLAY_STABILITY" -eq 1 ]; then
      [ "$DISPLAY_CRASH_COUNT" -eq 1 ] && set -- "$@" "$crash_count"
      [ "$DISPLAY_ANR_COUNT" -eq 1 ] && set -- "$@" "$anr_count"
      [ "$DISPLAY_LOGCAT_ERROR_COUNT" -eq 1 ] && set -- "$@" "$logcat_error_count"
    fi
    if [ "$DISPLAY_SYSTEM" -eq 1 ]; then
      [ "$DISPLAY_SYSTEM_CPU_PCT" -eq 1 ] && set -- "$@" "$system_cpu_pct"
      [ "$DISPLAY_SYSTEM_LOAD_1M" -eq 1 ] && set -- "$@" "$system_load_1m"
      [ "$DISPLAY_SYSTEM_MEM_TOTAL_MB" -eq 1 ] && set -- "$@" "$system_mem_total_mb"
      [ "$DISPLAY_SYSTEM_MEM_AVAILABLE_MB" -eq 1 ] && set -- "$@" "$system_mem_available_mb"
      [ "$DISPLAY_SYSTEM_STORAGE_FREE_KB" -eq 1 ] && set -- "$@" "$system_storage_free_kb"
    fi
    if [ "$DISPLAY_DEVICE" -eq 1 ]; then
      [ "$DISPLAY_DEVICE_SERIAL" -eq 1 ] && set -- "$@" "$DEVICE_SERIAL_CSV"
      [ "$DISPLAY_DEVICE_MANUFACTURER" -eq 1 ] && set -- "$@" "$DEVICE_MANUFACTURER_CSV"
      [ "$DISPLAY_DEVICE_BRAND" -eq 1 ] && set -- "$@" "$DEVICE_BRAND_CSV"
      [ "$DISPLAY_DEVICE_MODEL" -eq 1 ] && set -- "$@" "$DEVICE_MODEL_CSV"
      [ "$DISPLAY_ANDROID_VERSION" -eq 1 ] && set -- "$@" "$ANDROID_VERSION_CSV"
      [ "$DISPLAY_ANDROID_SDK" -eq 1 ] && set -- "$@" "$ANDROID_SDK_CSV"
      [ "$DISPLAY_BUILD_ID" -eq 1 ] && set -- "$@" "$BUILD_ID_CSV"
    fi
    [ "$DISPLAY_ADB_STATE" -eq 1 ] && set -- "$@" connected
    [ "$DISPLAY_STATUS" -eq 1 ] && set -- "$@" "$status"
    emit_csv_row "$@"
  done < "$PACKAGE_FILE"

  previous_total_ticks=$total_ticks
  printf '%s\t%s\n' __system__ "$idle_ticks" > "$CPU_PREV.tmp"
  cat "$CPU_CURR" >> "$CPU_PREV.tmp"
  mv "$CPU_PREV.tmp" "$CPU_PREV"
  mv "$IO_CURR" "$IO_PREV"
  [ "$refresh_storage" -eq 1 ] && next_storage=$((now+60))
  sleep "$INTERVAL"
done
