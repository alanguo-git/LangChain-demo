from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain.tools import tool
from langchain.agents import create_agent
from pydantic import BaseModel
import os
import requests
from dotenv import load_dotenv

# 加载.env文件中的环境变量
load_dotenv()

app = FastAPI()

# 挂载静态文件服务
app.mount("/static", StaticFiles(directory=".", html=True), name="static")

# 配置API密钥
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

# 定义用户上下文
class UserContext(BaseModel):
    user_id: str = "default"

# 初始化DeepSeek模型（使用OpenAI兼容接口）
if DEEPSEEK_API_KEY:
    chat_model = ChatOpenAI(
        api_key=DEEPSEEK_API_KEY,
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        streaming=True
    )
else:
    chat_model = None

# 天气查询工具
@tool
def get_weather(city: str):
    """获取指定城市的天气信息"""
    if not OPENWEATHER_API_KEY:
        return "Error: OpenWeather API key not configured"
    
    try:
        # 构建API请求URL
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city},cn&APPID={OPENWEATHER_API_KEY}&units=metric&lang=zh_cn"
        
        # 发送请求
        response = requests.get(url)
        data = response.json()
        
        # 检查响应状态
        if response.status_code == 200:
            # 提取天气信息
            status = data['weather'][0]['description']
            temp = data['main']['temp']
            humidity = data['main']['humidity']
            wind_speed = data['wind']['speed']
            
            return f"Weather in {city}: {status}, Temperature: {temp}°C, Humidity: {humidity}%, Wind: {wind_speed} m/s"
        else:
            return f"Error: {data.get('message', 'Failed to get weather data')}"
    except Exception as e:
        return f"Error: {str(e)}"

# 创建agent
if chat_model:
    agent = create_agent(
        chat_model,
        tools=[get_weather],
        context_schema=UserContext,
        system_prompt="你是一个智能助手，能够使用工具来回答用户的问题。当用户询问天气相关问题时，先将城市名称转换为英文，再使用get_weather工具来获取天气信息。"
    )
else:
    agent = None

@app.post("/chat")
async def chat(request: Request):
    """处理用户查询并返回流式响应"""
    if not chat_model:
        raise HTTPException(status_code=401, detail="DeepSeek API key not configured. Please set DEEPSEEK_API_KEY environment variable.")
    
    data = await request.json()
    query = data.get("query", "")
    
    if not query:
        return {"error": "Query is required"}
    
    # 定义流式生成器
    async def generate():
        try:
            async for chunk in chat_model.astream([HumanMessage(content=query)]):
                yield chunk.content
        except Exception as e:
            yield f"Error: {str(e)}"
    
    return StreamingResponse(generate(), media_type="text/plain")

@app.post("/agent")
async def agent_chat(request: Request):
    """使用Agent处理用户查询，支持天气查询功能"""
    if not agent:
        raise HTTPException(status_code=401, detail="Agent not configured. Please check API keys.")
    
    data = await request.json()
    query = data.get("query", "")
    
    if not query:
        return {"error": "Query is required"}
    
    # 定义流式生成器
    async def generate():
        try:
            async for chunk in agent.astream(
                {"messages": [{"role": "user", "content": query}]},
                context=UserContext(user_id="default"),
                stream_mode="updates",
            ):
                # 调试信息
                print(f"Received chunk: {type(chunk)}")
                print(f"Chunk content: {chunk}")
                
                # 处理不同类型的chunk
                if isinstance(chunk, dict):
                    # 检查是否有model字段
                    if "model" in chunk and isinstance(chunk["model"], dict):
                        model_data = chunk["model"]
                        if "messages" in model_data:
                            # 遍历消息，找到AIMessage
                            for message in model_data["messages"]:
                                if hasattr(message, "content") and message.content:
                                    content = message.content
                                    # 逐字符yield，实现真正的流式效果
                                    for char in content:
                                        yield char
                                        # 添加短暂延迟，模拟真实的流式效果
                                        import asyncio
                                        await asyncio.sleep(0.01)
                    # 检查是否有tools字段（工具执行结果）
                    elif "tools" in chunk and isinstance(chunk["tools"], dict):
                        tools_data = chunk["tools"]
                        if "messages" in tools_data:
                            # 遍历工具消息
                            for message in tools_data["messages"]:
                                if hasattr(message, "content") and message.content:
                                    # 工具执行结果可以不直接输出，由模型来总结
                                    pass
                elif hasattr(chunk, "content"):
                    yield chunk.content
                # 其他情况，直接转换为字符串
                else:
                    yield str(chunk)
                
        except Exception as e:
            print(f"Error: {str(e)}")
            yield f"Error: {str(e)}"
    
    return StreamingResponse(generate(), media_type="text/plain")

@app.get("/")
async def root():
    """返回前端页面"""
    with open("index.html", "r", encoding="utf-8") as f:
        content = f.read()
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=content)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)