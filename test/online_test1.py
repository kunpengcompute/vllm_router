from fastapi import FastAPI

from router.protocol import CompletionRequest

app = FastAPI()


@app.post("/v1/completions")
async def generate(body: CompletionRequest):
    body_ = body.model_dump()
    print(body)
    return "OK1"


@app.get("/health")
def alive():
    print("收到测活请求")
    return "OK"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
