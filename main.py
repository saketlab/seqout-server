from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pysradb import cli

app = FastAPI()

# CORS
origins = [
    "http://localhost",
    "http://localhost:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root(q: str = ""):
    if not q:
        return {"error": "missing 'q' query parameter"}
    try:
        df = cli.infer_search(
            saveto=None,
            verbosity=2,
            return_max=20,
            run_description=None,
            stats=None,
            graphs=None,
            detailed=None,
            phrase=q,
        )
        print(df.columns)
        return df.to_dict(orient="records")
    except Exception as e:
        return {"error": str(e)}
