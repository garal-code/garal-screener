import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.database import init_db
from app.routers.carteras import router as carteras_router
from app.routers.importaciones import router as importaciones_router
from app.routers.instrumentos import router as instrumentos_router
from app.routers.movimientos import router as movimientos_router
from app.routers.posiciones import router as posiciones_router

load_dotenv()  # carga .env en local; en Railway las vars vienen del entorno directamente


def _allowed_origins() -> list[str]:
    """
    Lee ALLOWED_ORIGINS del entorno.
    Acepta '*' o una lista de dominios separados por coma:
      ALLOWED_ORIGINS=https://garal.vercel.app,https://staging.garal.vercel.app
    """
    raw = os.getenv("ALLOWED_ORIGINS", "*")
    if raw.strip() == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Garal Cartera API",
    description="Backend para gestión de cartera de inversión personal",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(carteras_router, prefix="/api/v1")
app.include_router(instrumentos_router, prefix="/api/v1")
app.include_router(movimientos_router, prefix="/api/v1")
app.include_router(posiciones_router, prefix="/api/v1")
app.include_router(importaciones_router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}
