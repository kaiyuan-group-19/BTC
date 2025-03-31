from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
import uvicorn
import asyncio
import json
import websockets
import requests

app = FastAPI()

# # 挂载静态文件目录
# app.mount("/static", StaticFiles(directory="static"), name="static")

# 全局变量
clients = []  # 存储所有连接的 WebSocket 客户端
orderbook = {"bids": {}, "asks": {}}  # 本地订单簿副本
lastUpdateId = None  # 记录订单簿最新的更新 ID

# Binance API 和 WebSocket 地址
REST_API_URL = "https://api.binance.com/api/v3/depth"
WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@depth"

# WebSocket 连接
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()  # 监听客户端消息
    except Exception as e:
        print(f"Client disconnected: {e}")
    finally:
        clients.remove(websocket)

# 广播数据
async def broadcast(message: str):
    for client in clients:
        try:
            await client.send_text(message)
        except:
            clients.remove(client)

# 初始化订单簿
async def initialize_orderbook(symbol):
    global lastUpdateId, orderbook

    # 获取初始快照
    snapshot = fetch_initial_snapshot(symbol)
    if snapshot is None:
        print("Failed to fetch initial snapshot.")
        return

    lastUpdateId = snapshot["lastUpdateId"]

    for price, quantity in snapshot["bids"]:
        update_orderbook("bids", float(price), float(quantity))
    for price, quantity in snapshot["asks"]:
        update_orderbook("asks", float(price), float(quantity))

    print(f"Initialized orderbook with lastUpdateId: {lastUpdateId}")

    # 连接 Binance WebSocket 处理增量更新
    async with websockets.connect(WS_URL) as websocket:
        print(f"Connected to {WS_URL}")
        async for message in websocket:
            data = json.loads(message)
            handle_depth_update(data)
            await broadcast(json.dumps(orderbook))

# 获取快照
def fetch_initial_snapshot(symbol):
    try:
        params = {"symbol": symbol.upper(), "limit": 5000}
        response = requests.get(REST_API_URL, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch snapshot: {e}")
        return None

# 归一化订单簿（10 USDT 合并）
def update_orderbook(side, price, quantity):
    rounded_price = round(price / 10) * 10  # 价格归 10 USDT 合并
    if quantity == 0:
        orderbook[side].pop(rounded_price, None)
    else:
        orderbook[side][rounded_price] = orderbook[side].get(rounded_price, 0) + quantity

# 处理 Binance 增量深度更新
def handle_depth_update(data):
    global lastUpdateId
    U, u = data["U"], data["u"]

    if u < lastUpdateId:
        return

    if U > lastUpdateId + 1:
        print("WARNING: Missing updates, reinitializing orderbook.")
        asyncio.create_task(initialize_orderbook("BTCUSDT"))
        return

    for price, quantity in data["b"]:
        update_orderbook("bids", float(price), float(quantity))
    for price, quantity in data["a"]:
        update_orderbook("asks", float(price), float(quantity))

    lastUpdateId = u

# 运行服务器
async def run_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    await asyncio.gather(run_server(), initialize_orderbook("BTCUSDT"))

# if __name__ == "__main__":
#     asyncio.run(main())

asyncio.run(main())
