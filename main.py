import pandas as pd
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from queries import (
    GEO_SEARCH_SQL,
    PROJECT_GEO_SQL,
    PROJECT_SRA_SQL,
    SEARCH_SQL,
    SRA_SEARCH_SQL,
)

pool = ConnectionPool(
    "postgresql://postgres:postgres@localhost:5432/postgres",
    min_size=1,
    max_size=10,
    kwargs={"autocommit": True},
)


app = FastAPI()

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
def hello():
    return {"message": "pysradb-server is up and running!", "errors": None}


@app.get("/search/geo")
def search_geo(
    q: str,
    cursor_rank: float | None = Query(None),
    cursor_acc: str | None = Query(None),
):
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(GEO_SEARCH_SQL, (q, cursor_rank, cursor_rank, cursor_acc))
            rows = cur.fetchall()

    return {
        "results": rows,
        "next_cursor": None
        if not rows
        else {
            "rank": rows[-1]["rank"],
            "accession": rows[-1]["accession"],
        },
    }


@app.get("/search/sra")
def search_sra(
    q: str,
    cursor_rank: float | None = Query(None),
    cursor_acc: str | None = Query(None),
):
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SRA_SEARCH_SQL, (q, cursor_rank, cursor_rank, cursor_acc))
            rows = cur.fetchall()

    return {
        "results": rows,
        "next_cursor": None
        if not rows
        else {
            "rank": rows[-1]["accession"],
            "accession": rows[-1]["accession"],
        },
    }


@app.get("/search")
def search(
    q: str = Query(..., min_length=1),
    cursor_rank: float | None = Query(None),
    cursor_acc: str | None = Query(None),
    db: str | None = Query(None, pattern="^(geo|sra)$"),
):
    if db == "geo":
        return search_geo(q, 20, 0)
    elif db == "sra":
        return search_sra(q, 20, 0)

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SEARCH_SQL, (q, cursor_rank, cursor_rank, cursor_acc))
            rows = cur.fetchall()

    return {
        "results": rows,
        "next_cursor": None
        if not rows
        else {
            "rank": rows[-1]["rank"],
            "accession": rows[-1]["accession"],
        },
    }


@app.get("/project/{accession}")
def get_project(accession: str):
    sql = PROJECT_SRA_SQL if accession.upper().startswith("S") else PROJECT_GEO_SQL

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (accession,))
            row = cur.fetchone()

    return row
