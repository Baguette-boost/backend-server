#!/usr/bin/env bash
# 부하 중 서버측 리소스/커넥션 샘플링 (15초 * 23회 ≈ 5.75분)
OUT="/tmp/claude-1001/-home-ubuntu-backend-server/0ab48a26-2c5c-4d97-b850-4fba4b4faf37/scratchpad/monitor.log"
: > "$OUT"
for i in $(seq 1 23); do
  TS=$(date -u +%H:%M:%S)
  THREADS=$(docker exec -i baguetteboost-db mysql -uroot -prootpw -N -e "SHOW STATUS LIKE 'Threads_connected'" 2>/dev/null | awk '{print $2}')
  ABORTED=$(docker exec -i baguetteboost-db mysql -uroot -prootpw -N -e "SHOW STATUS LIKE 'Aborted_connects'" 2>/dev/null | awk '{print $2}')
  STATS=$(docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemPerc}}' \
      baguetteboost-backend baguetteboost-ai-wander baguetteboost-ai-fall baguetteboost-db 2>/dev/null)
  {
    echo "[$TS] db_threads=$THREADS aborted=$ABORTED"
    echo "$STATS" | sed 's/^/  /'
  } >> "$OUT"
  sleep 15
done
echo "=== monitor done ===" >> "$OUT"
