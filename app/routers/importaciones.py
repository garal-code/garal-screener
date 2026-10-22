from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth.security import get_current_user
from app.database import get_db

router = APIRouter(tags=["Importacion"])

NOMBRE_DUPLICADO = "Ya existe una plantilla con ese nombre"


def _plantilla_propia(
    plantilla_id: int, current_user: models.User, db: Session
) -> models.PlantillaImportacion:
    """404 — never 403 — for missing or foreign templates, like carteras."""
    plantilla = (
        db.query(models.PlantillaImportacion)
        .filter(
            models.PlantillaImportacion.id == plantilla_id,
            models.PlantillaImportacion.user_id == current_user.id,
        )
        .first()
    )
    if plantilla is None:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return plantilla


def _nombre_ocupado(
    db: Session, user_id: int, nombre: str, excepto_id: int | None = None
) -> bool:
    query = db.query(models.PlantillaImportacion).filter(
        models.PlantillaImportacion.user_id == user_id,
        models.PlantillaImportacion.nombre == nombre,
    )
    if excepto_id is not None:
        query = query.filter(models.PlantillaImportacion.id != excepto_id)
    return query.first() is not None


def _guardar(db: Session, plantilla: models.PlantillaImportacion) -> None:
    try:
        db.commit()
    except IntegrityError:
        # Concurrent request took the name between the check and the commit
        db.rollback()
        raise HTTPException(status_code=409, detail=NOMBRE_DUPLICADO) from None
    db.refresh(plantilla)


@router.get(
    "/plantillas-importacion", response_model=list[schemas.PlantillaImportacionOut]
)
def listar_plantillas(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.PlantillaImportacion)
        .filter(models.PlantillaImportacion.user_id == current_user.id)
        .order_by(models.PlantillaImportacion.nombre)
        .all()
    )


@router.post("/plantillas-importacion", response_model=schemas.PlantillaImportacionOut)
def crear_plantilla(
    data: schemas.PlantillaImportacionIn,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if _nombre_ocupado(db, current_user.id, data.nombre):
        raise HTTPException(status_code=409, detail=NOMBRE_DUPLICADO)
    plantilla = models.PlantillaImportacion(
        user_id=current_user.id,
        nombre=data.nombre,
        config=data.config.model_dump(mode="json"),
    )
    db.add(plantilla)
    _guardar(db, plantilla)
    return plantilla


@router.put(
    "/plantillas-importacion/{plantilla_id}",
    response_model=schemas.PlantillaImportacionOut,
)
def actualizar_plantilla(
    plantilla_id: int,
    data: schemas.PlantillaImportacionIn,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plantilla = _plantilla_propia(plantilla_id, current_user, db)
    if _nombre_ocupado(db, current_user.id, data.nombre, excepto_id=plantilla.id):
        raise HTTPException(status_code=409, detail=NOMBRE_DUPLICADO)
    plantilla.nombre = data.nombre
    plantilla.config = data.config.model_dump(mode="json")
    _guardar(db, plantilla)
    return plantilla


@router.delete("/plantillas-importacion/{plantilla_id}")
def eliminar_plantilla(
    plantilla_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plantilla = _plantilla_propia(plantilla_id, current_user, db)
    db.delete(plantilla)
    db.commit()
    return {"ok": True}
