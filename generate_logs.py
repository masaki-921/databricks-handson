#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ハンズオン用サンプルログ生成スクリプト
- Oracle アラートログ風 (alert_ORCL.log)
- Java アプリケーションログ風 (app.log)

ストーリー:
  2026-06-20 の一日分のログ。
  10:0x 頃に Oracle 側でデッドロック(ORA-00060)→接続枯渇(ORA-12516/ORA-12519)が発生し、
  ほぼ同時刻に Java アプリ側で SQLException / 接続タイムアウトが急増するインシデントを仕込む。
  Gold 層で時間窓集計するとこの相関が浮かび上がる、という流れ。
"""
import random
from datetime import datetime, timedelta

random.seed(20260620)

DAY = datetime(2026, 6, 20, 0, 0, 0)

# ---------------------------------------------------------------------------
# 1. Oracle アラートログ
# ---------------------------------------------------------------------------
def oracle_ts(dt):
    # 例: 2026-06-20T08:15:03.123456+09:00
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "+09:00"

oracle_lines = []

def ora(dt, msg):
    oracle_lines.append(f"{oracle_ts(dt)}")
    oracle_lines.append(msg)

# --- 起動・通常運用メッセージ ---
ora(DAY + timedelta(hours=6, minutes=0, seconds=2),
    "Starting ORACLE instance (normal) (OS id: 4821)")
ora(DAY + timedelta(hours=6, minutes=0, seconds=8),
    "Database mounted in Exclusive Mode")
ora(DAY + timedelta(hours=6, minutes=0, seconds=15),
    "Completed: ALTER DATABASE OPEN")
ora(DAY + timedelta(hours=6, minutes=30, seconds=0),
    "Thread 1 advanced to log sequence 14213 (LGWR switch)")

# 通常運用中のログスイッチ等を一日に散りばめる
normal_msgs = [
    "Thread 1 advanced to log sequence {seq} (LGWR switch)",
    "Archived Log entry {seq} added for T-1.S-{seq} ID 0x6a3f LAD:1",
    "Resize operation completed for file# 3, filesize {fs}M",
    "TABLE SYS.WRH$_ACTIVE_SESSION_HISTORY: ADDED INTERVAL PARTITION",
]
seq = 14214
t = DAY + timedelta(hours=7)
while t < DAY + timedelta(hours=22):
    m = random.choice(normal_msgs)
    ora(t, m.format(seq=seq, fs=random.choice([512, 1024, 2048])))
    seq += 1
    t += timedelta(minutes=random.randint(18, 40))

# --- 散発的な軽微エラー(ノイズ) ---
ora(DAY + timedelta(hours=2, minutes=11, seconds=5),
    "ORA-01555: snapshot too old: rollback segment number 12 with name \"_SYSSMU12_\" too small")
ora(DAY + timedelta(hours=14, minutes=3, seconds=41),
    "ORA-00060: Deadlock detected. More info in file /u01/diag/rdbms/orcl/orcl/trace/orcl_ora_9912.trc.")

# --- インシデント本体: 10:0x 台にデッドロック→接続枯渇が集中 ---
incident_base = DAY + timedelta(hours=10, minutes=2)
incident_events = [
    (0,  "ORA-00060: Deadlock detected. More info in file /u01/diag/rdbms/orcl/orcl/trace/orcl_ora_10233.trc."),
    (7,  "ORA-00060: Deadlock detected. More info in file /u01/diag/rdbms/orcl/orcl/trace/orcl_ora_10241.trc."),
    (12, "ORA-12516: TNS:listener could not find available handler with matching protocol stack"),
    (15, "ORA-12519: TNS:no appropriate service handler found"),
    (16, "ORA-12516: TNS:listener could not find available handler with matching protocol stack"),
    (19, "ORA-00060: Deadlock detected. More info in file /u01/diag/rdbms/orcl/orcl/trace/orcl_ora_10258.trc."),
    (21, "ORA-12519: TNS:no appropriate service handler found"),
    (24, "ORA-12516: TNS:listener could not find available handler with matching protocol stack"),
    (28, "ORA-04031: unable to allocate 4160 bytes of shared memory (\"shared pool\",\"select ...\",\"sga heap\")"),
    (33, "ORA-12516: TNS:listener could not find available handler with matching protocol stack"),
]
for off_sec, msg in incident_events:
    ora(incident_base + timedelta(seconds=off_sec * 7 + random.randint(0, 4)), msg)

# --- 夜間バッチ中の単発エラー ---
ora(DAY + timedelta(hours=23, minutes=47, seconds=12),
    "ORA-01652: unable to extend temp segment by 128 in tablespace TEMP")

# 時系列順に並べ替え(タイムスタンプ行 + 本文行のペアなので2行単位で処理)
pairs = list(zip(oracle_lines[0::2], oracle_lines[1::2]))
pairs.sort(key=lambda p: p[0])
oracle_out = []
for ts_line, body in pairs:
    oracle_out.append(ts_line)
    oracle_out.append(body)

with open("data/alert_ORCL.log", "w", encoding="utf-8") as f:
    f.write("\n".join(oracle_out) + "\n")

print(f"alert_ORCL.log: {len(oracle_out)} lines")

# ---------------------------------------------------------------------------
# 2. Java アプリケーションログ (Logback / log4j 形式)
# ---------------------------------------------------------------------------
# 形式: 2026-06-20 10:23:45,123 ERROR [http-nio-8080-exec-7] c.e.OrderService - message
def java_ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S,") + f"{dt.microsecond // 1000:03d}"

java_lines = []

threads = [f"http-nio-8080-exec-{i}" for i in range(1, 13)] + ["scheduler-1", "async-task-3"]
loggers = [
    "c.e.web.OrderController",
    "c.e.service.OrderService",
    "c.e.service.PaymentService",
    "c.e.repository.OrderRepository",
    "c.e.config.DataSourceHealth",
    "org.hibernate.engine.jdbc.spi.SqlExceptionHelper",
]

info_msgs = [
    "Received request POST /api/orders userId={uid}",
    "Order {oid} created successfully amount={amt}",
    "Payment authorized for order {oid}",
    "Cache hit for product catalog page={p}",
    "Healthcheck OK pool.active={a} pool.idle={i}",
]
warn_msgs = [
    "Slow query detected took={ms}ms threshold=1000ms",
    "Connection pool nearing capacity active={a}/20",
    "Retrying operation attempt={n}/3",
]

def jlog(dt, level, logger, msg, thread=None, stack=None):
    th = thread or random.choice(threads)
    java_lines.append((dt, f"{java_ts(dt)} {level:5s} [{th}] {logger} - {msg}"))
    if stack:
        for s in stack:
            java_lines.append((dt, s))

# --- 通常運用ログを一日散りばめる ---
t = DAY + timedelta(hours=6, minutes=5)
oid = 50001
while t < DAY + timedelta(hours=23, minutes=30):
    r = random.random()
    if r < 0.80:
        m = random.choice(info_msgs).format(
            uid=random.randint(1000, 9999), oid=oid, amt=random.randint(1000, 50000),
            p=random.randint(1, 30), a=random.randint(2, 12), i=random.randint(1, 8))
        jlog(t, "INFO", random.choice(loggers), m)
        oid += 1
    elif r < 0.92:
        m = random.choice(warn_msgs).format(
            ms=random.randint(1000, 4000), a=random.randint(15, 19), n=random.randint(1, 3))
        jlog(t, "WARN", random.choice(loggers), m)
    else:
        # 平常時の単発 ERROR (ノイズ)
        jlog(t, "ERROR", "c.e.web.OrderController",
             "Unhandled validation error: field 'email' must not be blank",
             stack=[
                 "java.lang.IllegalArgumentException: field 'email' must not be blank",
                 "\tat c.e.web.OrderController.validate(OrderController.java:88)",
                 "\tat c.e.web.OrderController.create(OrderController.java:52)",
             ])
    t += timedelta(seconds=random.randint(20, 90))

# --- インシデント: 10:02〜10:09 に SQLException / タイムアウトが急増 ---
inc = DAY + timedelta(hours=10, minutes=2, seconds=10)
sql_stack = [
    "org.springframework.dao.CannotAcquireLockException: could not execute statement [ORA-00060: deadlock detected]",
    "\tat o.h.engine.jdbc.spi.SqlExceptionHelper.convert(SqlExceptionHelper.java:113)",
    "\tat c.e.repository.OrderRepository.save(OrderRepository.java:144)",
    "\tat c.e.service.OrderService.placeOrder(OrderService.java:71)",
    "Caused by: java.sql.SQLException: ORA-00060: deadlock detected while waiting for resource",
    "\tat oracle.jdbc.driver.T4CTTIoer11.processError(T4CTTIoer11.java:494)",
]
pool_stack = [
    "java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms",
    "\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:213)",
    "\tat c.e.repository.OrderRepository.findById(OrderRepository.java:97)",
    "Caused by: java.sql.SQLException: ORA-12516: TNS:listener could not find available handler",
]
for k in range(22):
    dt = inc + timedelta(seconds=random.randint(0, 430))
    if random.random() < 0.55:
        jlog(dt, "ERROR", "c.e.service.OrderService",
             "Failed to place order, rolling back transaction orderId={}".format(60000 + k),
             stack=sql_stack)
    else:
        jlog(dt, "ERROR", "c.e.repository.OrderRepository",
             "Connection acquisition failed for read replica",
             stack=pool_stack)
# インシデント直後の回復 WARN
jlog(inc + timedelta(minutes=9), "WARN", "c.e.config.DataSourceHealth",
     "Connection pool recovering active=14/20")

# --- 夜間バッチの NullPointer 単発 ---
jlog(DAY + timedelta(hours=2, minutes=30, seconds=5), "ERROR", "c.e.service.PaymentService",
     "Batch settlement failed for file=settle_20260619.csv",
     thread="scheduler-1",
     stack=[
         "java.lang.NullPointerException: Cannot invoke \"String.trim()\" because \"row[3]\" is null",
         "\tat c.e.service.PaymentService.parseRow(PaymentService.java:210)",
         "\tat c.e.service.PaymentService.runBatch(PaymentService.java:160)",
     ])

# 時系列順に並べ替え
java_lines.sort(key=lambda x: x[0])
with open("data/app.log", "w", encoding="utf-8") as f:
    f.write("\n".join(line for _, line in java_lines) + "\n")

print(f"app.log: {len(java_lines)} lines")
