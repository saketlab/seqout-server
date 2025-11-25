import pandas as pd
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pysradb import SRAweb, cli

app = FastAPI()
client = SRAweb()

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


def format_df(df: pd.DataFrame | None):
    if df is not None:
        grouped_by_srp = (
            df.groupby("study_accession")
            .apply(
                lambda g: g.drop(columns="study_accession").to_dict(orient="records")
            )
            .to_dict()
        )
        return grouped_by_srp
    else:
        return {"error": "No dataframe returned"}


@app.get("/")
def read_root():
    return {"message": "Namaste! pysradb-server is up and running!"}


@app.get("/search")
def search(q: str = ""):
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
        return format_df(df)
    except Exception as e:
        return {"error": str(e)}


@app.get("/experiments")
def get_experiments(srp: str):
    if not srp:
        return {"error": "missing 'srp' query parameter"}
    try:
        df = client.sra_metadata(srp)
        return format_df(df)
    except Exception as e:
        return {"error": str(e)}
