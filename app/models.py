from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship

Base = declarative_base()

TIPOS_MOVIMIENTO = {"compra", "venta"}
TIPOS_INSTRUMENTO = {"accion", "etf", "fondo", "otro"}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    nombre: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    carteras: Mapped[list["Cartera"]] = relationship("Cartera", back_populates="owner")
    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        "PasswordResetToken", back_populates="user", cascade="all, delete-orphan"
    )
    plantillas_importacion: Mapped[list["PlantillaImportacion"]] = relationship(
        "PlantillaImportacion", back_populates="user", cascade="all, delete-orphan"
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    user: Mapped["User"] = relationship("User", back_populates="reset_tokens")


class Cartera(Base):
    __tablename__ = "carteras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String)
    descripcion: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now())

    owner: Mapped["User"] = relationship("User", back_populates="carteras")
    # cascade="all, delete-orphan" -> al borrar cartera se borran sus movimientos
    movimientos: Mapped[list["Movimiento"]] = relationship(
        "Movimiento", back_populates="cartera", cascade="all, delete-orphan"
    )


class Instrumento(Base):
    __tablename__ = "instrumentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    isin: Mapped[str] = mapped_column(String, unique=True, index=True)
    ticker: Mapped[str | None] = mapped_column(String)
    nombre: Mapped[str | None] = mapped_column(String)
    tipo: Mapped[str | None] = mapped_column(String)  # accion | etf | fondo | otro
    sector: Mapped[str | None] = mapped_column(String)
    pais: Mapped[str | None] = mapped_column(String)
    moneda: Mapped[str | None] = mapped_column(String)  # EUR, USD, JPY...
    exchange: Mapped[str | None] = mapped_column(String)  # NYSE, BME, XETRA...
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.now(), onupdate=datetime.now()
    )

    movimientos: Mapped[list["Movimiento"]] = relationship(
        "Movimiento", back_populates="instrumento"
    )


class Movimiento(Base):
    __tablename__ = "movimientos"
    # A broker ID is imported at most once per cartera (NULLs never collide)
    __table_args__ = (
        UniqueConstraint(
            "cartera_id",
            "referencia_externa",
            name="uq_movimientos_cartera_referencia_externa",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cartera_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("carteras.id", ondelete="CASCADE")
    )
    instrumento_id: Mapped[int] = mapped_column(Integer, ForeignKey("instrumentos.id"))
    tipo: Mapped[str] = mapped_column(String)  # compra | venta
    fecha: Mapped[date] = mapped_column(Date)
    cantidad: Mapped[float] = mapped_column(Float)
    precio: Mapped[float] = mapped_column(Float)  # en moneda original del instrumento
    comision: Mapped[float] = mapped_column(Float, default=0.0)  # opcional, default 0
    tipo_cambio: Mapped[float | None] = mapped_column(
        Float
    )  # opcional, EUR/moneda en la fecha
    notas: Mapped[str | None] = mapped_column(Text)
    # Broker transaction ID when the movement comes from a CSV import
    referencia_externa: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now())

    cartera: Mapped["Cartera"] = relationship("Cartera", back_populates="movimientos")
    instrumento: Mapped["Instrumento"] = relationship(
        "Instrumento", back_populates="movimientos"
    )


class PlantillaImportacion(Base):
    """Saved CSV column mapping of one user (see schemas.PlantillaConfig)."""

    __tablename__ = "plantillas_importacion"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "nombre", name="uq_plantillas_importacion_user_nombre"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    nombre: Mapped[str] = mapped_column(String)
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    user: Mapped["User"] = relationship("User", back_populates="plantillas_importacion")
