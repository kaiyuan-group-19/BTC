from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import json
import websockets
import requests
from datetime import datetime
import os

app = FastAPI()

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局变量
clients = []
orderbook = {"bids": {}, "asks": {}}
lastUpdateId = None

# Binance API配置
# REST_API_URL = "https://api.binance.com/api/v3/depth"
REST_API_URL = "https://misty-paper-da8d.1585953703.workers.dev"
WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@depth"

class ConnectionManager:
    def __init__(self):
        self.active_connections = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"New client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        print(f"Client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                self.disconnect(connection)

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # 立即发送当前订单簿状态
        if orderbook["bids"] and orderbook["asks"]:
            await websocket.send_text(json.dumps({
                **orderbook,
                "timestamp": datetime.utcnow().isoformat()
            }))

        while True:
            # 保持连接活跃
            await websocket.receive_text()
            await asyncio.sleep(5)  # 心跳间隔

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        manager.disconnect(websocket)

async def fetch_initial_snapshot(symbol="BTCUSDT"):
    try:
        params = {"symbol": symbol, "limit": 5000}
        response = requests.get(REST_API_URL, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Snapshot error: {str(e)}")
        return None

# PROXY_URL = os.getenv("PROXY_URL")  # 例如: "http://user:pass@proxyip:port"

# async def fetch_initial_snapshot(symbol="BTCUSDT"):
#     try:
#         params = {"symbol": symbol, "limit": 100}  # 减少limit值
#         proxies = {
#             "http": PROXY_URL,
#             "https": PROXY_URL
#         } if PROXY_URL else None
        
#         response = requests.get(
#             REST_API_URL,
#             params=params,
#             proxies=proxies,
#             timeout=10
#         )
#         response.raise_for_status()
#         return response.json()
#     except Exception as e:
#         print(f"Snapshot error: {str(e)}")
#         return None

def normalize_price(price):
    return round(float(price) / 10) * 10  # 按10美元分组

def update_orderbook(data):
    global lastUpdateId, orderbook
    
    # 更新买单
    for price, quantity in data.get('b', []):
        norm_price = normalize_price(price)
        if float(quantity) == 0:
            orderbook["bids"].pop(norm_price, None)
        else:
            orderbook["bids"][norm_price] = orderbook["bids"].get(norm_price, 0) + float(quantity)
    
    # 更新卖单
    for price, quantity in data.get('a', []):
        norm_price = normalize_price(price)
        if float(quantity) == 0:
            orderbook["asks"].pop(norm_price, None)
        else:
            orderbook["asks"][norm_price] = orderbook["asks"].get(norm_price, 0) + float(quantity)

async def binance_websocket_listener():
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                print("Connected to Binance WebSocket")
                async for message in ws:
                    data = json.loads(message)
                    update_orderbook(data)
                    await manager.broadcast(json.dumps({
                        **orderbook,
                        "timestamp": datetime.utcnow().isoformat()
                    }))
        except Exception as e:
            print(f"Binance connection lost: {str(e)}. Reconnecting in 5s...")
            await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    # 初始化订单簿
    snapshot = await fetch_initial_snapshot()
    if snapshot:
        global lastUpdateId
        lastUpdateId = snapshot["lastUpdateId"]
        update_orderbook({"b": snapshot["bids"], "a": snapshot["asks"]})
    
    # 启动Binance监听
    asyncio.create_task(binance_websocket_listener())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
