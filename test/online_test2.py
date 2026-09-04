from fastapi import FastAPI

app = FastAPI()


@app.post("/v1/completions")
async def generate(body):
    print(body)
    return "OK2"


@app.get("/health")
def alive():
    print("收到测活请求")
    return "OK"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
