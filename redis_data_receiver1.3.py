import os, socket, struct, time
from datetime import datetime, time as dt_time

import redis


TCP_HOST = os.getenv("TCP_HOST", "qt1.chagubang.com")
TCP_PORT = int(os.getenv("TCP_PORT", "8380"))
TCP_TOKEN = os.getenv("TCP_TOKEN", "HS_QTkBkpzKuchvcK3E")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

BATCH_SIZE = 2000
FLUSH_INTERVAL_SECONDS = 1.0
SOCKET_TIMEOUT_SECONDS = 5.0
RECV_TOTAL_TIMEOUT_SECONDS = 10.0
DRAIN_AFTER_CLOSE_SECONDS = 12

MAX_RETRIES = 3
COOLDOWN_PERIOD = 1800

MORNING_SESSION_START = dt_time(9, 15)
MORNING_SESSION_END = dt_time(11, 30)
AFTERNOON_SESSION_START = dt_time(13, 0)
AFTERNOON_SESSION_END = dt_time(15, 0, 30)


from datetime import timezone, timedelta

CST = timezone(timedelta(hours=8))

def _now() -> datetime:
    return datetime.now(CST)

def is_market_open() -> bool:
    now = _now().time()
    in_morning = MORNING_SESSION_START <= now <= MORNING_SESSION_END
    in_afternoon = AFTERNOON_SESSION_START <= now <= AFTERNOON_SESSION_END
    return in_morning or in_afternoon


def in_close_drain_window() -> bool:
    now = _now().time()
    drain_end = dt_time(15, 0, 30 + DRAIN_AFTER_CLOSE_SECONDS)
    return AFTERNOON_SESSION_END < now <= drain_end


def should_receive_data() -> bool:
    return is_market_open() or in_close_drain_window()


def recvall(sock: socket.socket, n: int) -> bytes | None:
    data = bytearray()
    deadline = time.time() + RECV_TOTAL_TIMEOUT_SECONDS
    while len(data) < n:
        if time.time() > deadline:
            return None
        try:
            packet = sock.recv(n - len(data))
            if not packet:
                return None
            data.extend(packet)
        except socket.timeout:
            time.sleep(0.05)
        except BlockingIOError:
            time.sleep(0.05)
    return bytes(data)


def receive_message(sock: socket.socket) -> bytes | None:
    raw_msg_len = recvall(sock, 4)
    if not raw_msg_len or len(raw_msg_len) < 4:
        return None
    msg_len = struct.unpack("<I", raw_msg_len)[0]
    if msg_len <= 0:
        return None
    return recvall(sock, msg_len)


def build_redis() -> redis.Redis:
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def flush_pipe(pipe: redis.client.Pipeline, pending_count: int, reason: str) -> int:
    if pending_count <= 0:
        return 0
    pipe.execute()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {pending_count} 条数据已写入Redis（{reason}）。")
    return 0


def normalize_stock_code(decoded_message: str) -> str:
    return decoded_message.split("$", 1)[0].replace("SZ", "").replace("SH", "").strip()


def main() -> None:
    print("--- 启动 TCP -> Redis 数据接收服务 ---")

    try:
        redis_client = build_redis()
        redis_client.ping()
        print(f"成功连接到 Redis: {REDIS_HOST}:{REDIS_PORT}")
    except redis.exceptions.ConnectionError as exc:
        print(f"致命错误：无法连接到 Redis。请确认 Redis 正在运行。错误: {exc}")
        return

    while True:
        if not is_market_open():
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 当前非交易时段，暂停连接尝试...")
            while not is_market_open():
                time.sleep(15)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 市场已开盘，恢复数据连接...")

        for attempt in range(1, MAX_RETRIES + 1):
            sock = None
            pipe = None
            message_batch_count = 0
            last_flush_ts = time.time()
            try:
                if not is_market_open():
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 市场已关闭，跳过本次连接尝试。")
                    break

                redis_client = build_redis()
                pipe = redis_client.pipeline()

                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(SOCKET_TIMEOUT_SECONDS)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在进行第 {attempt}/{MAX_RETRIES} 次连接尝试...")
                sock.connect((TCP_HOST, TCP_PORT))
                sock.sendall(TCP_TOKEN.encode("utf-8"))
                print("连接成功，开始接收数据...")

                while True:
                    if not should_receive_data():
                        message_batch_count = flush_pipe(pipe, message_batch_count, "收盘排空")
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] 交易时段已结束，主动断开连接。")
                        break

                    message = receive_message(sock)
                    if message is None:
                        message_batch_count = flush_pipe(pipe, message_batch_count, "连接中断前落盘")
                        print("接收数据超时或连接中断，准备重连...")
                        break

                    try:
                        decoded_message = message.decode("utf-8", errors="ignore")
                        stock_code = normalize_stock_code(decoded_message)
                        if stock_code:
                            pipe.set(stock_code, decoded_message)
                            message_batch_count += 1

                        now_ts = time.time()
                        if message_batch_count >= BATCH_SIZE:
                            message_batch_count = flush_pipe(pipe, message_batch_count, "批量阈值")
                            last_flush_ts = now_ts
                        elif message_batch_count > 0 and (now_ts - last_flush_ts) >= FLUSH_INTERVAL_SECONDS:
                            message_batch_count = flush_pipe(pipe, message_batch_count, "定时刷新")
                            last_flush_ts = now_ts
                    except Exception:
                        continue

                break

            except (socket.error, ConnectionRefusedError, TimeoutError) as exc:
                print(f"连接尝试 {attempt} 失败: {exc}")
                if attempt < MAX_RETRIES:
                    time.sleep(5)
                continue
            except redis.exceptions.ConnectionError as exc:
                print(f"Redis 连接错误: {exc}。将在 5 秒后重试...")
                time.sleep(5)
            except Exception as exc:
                print(f"发生未知错误: {exc}。将在 5 秒后重试...")
                time.sleep(5)
            finally:
                if pipe is not None and message_batch_count > 0:
                    try:
                        message_batch_count = flush_pipe(pipe, message_batch_count, "finally收尾")
                    except Exception as redis_exc:
                        print(f"写入剩余数据到 Redis 时出错: {redis_exc}")
                if sock is not None:
                    sock.close()
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 连续 {MAX_RETRIES} 次连接失败，进入冷却 {COOLDOWN_PERIOD} 秒。")
            time.sleep(COOLDOWN_PERIOD)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 冷却结束，重新开始。")


if __name__ == "__main__":
    main()
