import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth.security import get_current_user, get_owned_cartera
from app.database import get_db
from app.services import importacion, precios

router = APIRouter(tags=["Importacion"])

NOMBRE_DUPLICADO = "Ya existe una plantilla con ese nombre"
HUELLA_OBSOLETA = (
    "La previsualización ya no coincide con los datos actuales. "
    "Vuelve a previsualizar."
)


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


# -- Import: inspect, preview, confirm ------------------------------------------


@router.post("/importaciones/inspeccionar", response_model=schemas.InspeccionarOut)
def inspeccionar_csv(
    data: schemas.InspeccionarIn,
    current_user: models.User = Depends(get_current_user),
):
    try:
        return importacion.inspeccionar(data.contenido, data.delimitador)
    except importacion.ImportacionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


async def _planificar(
    data: schemas.PrevisualizarImportacionIn,
    cartera: models.Cartera,
    current_user: models.User,
    db: Session,
) -> importacion.Plan:
    """Read the CSV and decide each row. Only reads from the database."""
    plantilla = _plantilla_propia(data.plantilla_id, current_user, db)
    config = schemas.PlantillaConfig.model_validate(plantilla.config)
    try:
        resultados = importacion.leer_y_normalizar(data.contenido, config)
    except importacion.ImportacionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None

    isines = {r.isin for r in resultados if isinstance(r, importacion.FilaNormalizada)}
    conocidos = {
        i.isin: i.nombre
        for i in db.query(models.Instrumento).filter(
            models.Instrumento.isin.in_(isines)
        )
    }
    referencias = {
        ref
        for (ref,) in db.query(models.Movimiento.referencia_externa).filter(
            models.Movimiento.cartera_id == cartera.id,
            models.Movimiento.referencia_externa.isnot(None),
        )
        if ref
    }
    existentes: dict[str, list] = {}
    for movimiento in (
        db.query(models.Movimiento)
        .join(models.Instrumento)
        .filter(
            models.Movimiento.cartera_id == cartera.id,
            models.Instrumento.isin.in_(isines),
        )
    ):
        existentes.setdefault(movimiento.instrumento.isin, []).append(movimiento)

    return await importacion.planificar(
        resultados, referencias, conocidos, existentes, precios.enriquecer_por_isin
    )


def _filas_salida(plan: importacion.Plan) -> list[schemas.FilaImportacionOut]:
    filas = []
    for p in plan.filas:
        f = p.fila
        filas.append(
            schemas.FilaImportacionOut(
                linea=p.linea,
                estado=p.estado,  # type: ignore[arg-type]
                motivo=p.motivo,
                tipo=f.tipo if f else None,
                fecha=f.fecha if f else None,
                isin=f.isin if f else None,
                nombre=p.nombre,
                cantidad=f.cantidad if f else None,
                precio=f.precio if f else None,
                comision=f.comision if f else None,
                tipo_cambio=f.tipo_cambio if f else None,
                referencia=f.referencia if f else None,
            )
        )
    return filas


def _huella(filas: list[schemas.FilaImportacionOut]) -> str:
    canonico = json.dumps(
        [f.model_dump(mode="json") for f in filas],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonico.encode()).hexdigest()


@router.post(
    "/carteras/{cartera_id}/importaciones/previsualizar",
    response_model=schemas.PrevisualizacionImportacionOut,
)
async def previsualizar_importacion(
    data: schemas.PrevisualizarImportacionIn,
    cartera: models.Cartera = Depends(get_owned_cartera),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Explains what an import would do. Writes nothing."""
    plan = await _planificar(data, cartera, current_user, db)
    filas = _filas_salida(plan)
    importar = sum(1 for f in filas if f.estado == "importar")
    return schemas.PrevisualizacionImportacionOut(
        huella=_huella(filas),
        resumen=schemas.ResumenImportacionOut(
            total=len(filas), importar=importar, omitir=len(filas) - importar
        ),
        filas=filas,
    )


@router.post(
    "/carteras/{cartera_id}/importaciones",
    response_model=schemas.ResultadoImportacionOut,
)
async def confirmar_importacion(
    data: schemas.ConfirmarImportacionIn,
    cartera: models.Cartera = Depends(get_owned_cartera),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Recomputes the plan and, if it is the one previewed, writes it at once."""
    plan = await _planificar(data, cartera, current_user, db)
    if _huella(_filas_salida(plan)) != data.huella:
        raise HTTPException(status_code=409, detail=HUELLA_OBSOLETA)

    # Processing order, not file order: autoincrement ids must follow the time
    a_importar = plan.importables
    try:
        instrumentos: dict[str, models.Instrumento] = {
            i.isin: i
            for i in db.query(models.Instrumento).filter(
                models.Instrumento.isin.in_({f.isin for f in a_importar})
            )
        }
        for isin in sorted({f.isin for f in a_importar} - instrumentos.keys()):
            datos = plan.enriquecidos[isin]
            instrumentos[isin] = models.Instrumento(
                isin=isin,
                ticker=datos.get("ticker"),
                nombre=datos.get("nombre"),
                tipo=datos.get("tipo"),
                sector=datos.get("sector"),
                pais=datos.get("pais"),
                moneda=datos.get("moneda"),
                exchange=datos.get("exchange"),
            )
            db.add(instrumentos[isin])
        db.flush()  # instrument ids, still inside the transaction

        for f in a_importar:
            db.add(
                models.Movimiento(
                    cartera_id=cartera.id,
                    instrumento_id=instrumentos[f.isin].id,
                    tipo=f.tipo,
                    fecha=f.fecha,
                    cantidad=f.cantidad,
                    precio=f.precio,
                    comision=f.comision,
                    tipo_cambio=f.tipo_cambio,
                    notas=None,
                    referencia_externa=f.referencia,
                )
            )
        db.commit()
    except IntegrityError:
        # Another request took an ISIN or a broker ID since the plan was made
        db.rollback()
        raise HTTPException(status_code=409, detail=HUELLA_OBSOLETA) from None
    except Exception:
        db.rollback()
        raise
    return schemas.ResultadoImportacionOut(
        importados=len(a_importar), omitidos=len(plan.filas) - len(a_importar)
    )
