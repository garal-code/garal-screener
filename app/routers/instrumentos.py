from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.services import precios

router = APIRouter(prefix="/instrumentos", tags=["Instrumentos"])


@router.get("/autodescubrir/{isin}", response_model=schemas.InstrumentoOut)
async def autodescubrir(isin: str, db: Session = Depends(get_db)):
    """Busca o crea un instrumento por ISIN para metadatos."""
    isin = isin.strip().upper()
    instrumento = (
        db.query(models.Instrumento).filter(models.Instrumento.isin == isin).first()
    )
    if instrumento:
        return instrumento

    datos = await precios.enriquecer_por_isin(isin)
    if not datos:
        raise HTTPException(
            status_code=404,
            detail=f"No se pudieron obtener datos para el ISIN {isin}. Comprueba que el ISIN es correcto.",
        )

    instrumento = models.Instrumento(
        isin=isin,
        ticker=datos.get("ticker"),
        nombre=datos.get("nombre"),
        tipo=datos.get("tipo"),
        sector=datos.get("sector"),
        pais=datos.get("pais"),
        moneda=datos.get("moneda"),
        exchange=datos.get("exchange"),
    )
    db.add(instrumento)
    db.commit()
    db.refresh(instrumento)
    return instrumento


@router.get("", response_model=list[schemas.InstrumentoOut])
def listar_instrumentos(db: Session = Depends(get_db)):
    return db.query(models.Instrumento).all()


@router.patch("/{instrumento_id}", response_model=schemas.InstrumentoOut)
def actualizar_instrumento(
    instrumento_id: int,
    data: schemas.InstrumentoUpdate,
    db: Session = Depends(get_db),
):
    """Permite corregir manualmente cualquier campo del instrumento."""
    instrumento = (
        db.query(models.Instrumento)
        .filter(models.Instrumento.id == instrumento_id)
        .first()
    )
    if not instrumento:
        raise HTTPException(status_code=404, detail="Instrumento no encontrado")
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(instrumento, campo, valor)
    db.commit()
    db.refresh(instrumento)
    return instrumento


@router.delete("")
def eliminar_todos_instrumentos(db: Session = Depends(get_db)):
    db.query(models.Instrumento).delete()
    db.commit()
    return {"ok": True}


@router.delete("/{instrumento_id}")
def eliminar_instrumento(instrumento_id: int, db: Session = Depends(get_db)):
    instrumento = (
        db.query(models.Instrumento)
        .filter(models.Instrumento.id == instrumento_id)
        .first()
    )
    if not instrumento:
        raise HTTPException(status_code=404, detail="Instrumento no encontrado")
    db.delete(instrumento)
    db.commit()
    return {"ok": True}
