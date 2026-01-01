from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from queries import GEO_SEARCH_SQL, SEARCH_SQL, SRA_SEARCH_SQL

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


@app.get("/")
def hello():
    return {"message": "pysradb-server is up and running!", "errors": None}


@app.get("/search")
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SEARCH_SQL, (q, limit, offset))
            return cur.fetchall()


@app.get("/search/geo")
def search_geo(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(GEO_SEARCH_SQL, (q, limit, offset))
            return cur.fetchall()


@app.get("/search/sra")
def search_sra(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SRA_SEARCH_SQL, (q, limit, offset))
            return cur.fetchall()
