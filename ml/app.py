"""API local del análisis predictivo. Iniciar con uvicorn app:app --port 8001."""
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from engine import read_report, train

app = FastAPI(title='Análisis predictivo AYALA', version='1.0.0')


class TrainingData(BaseModel):
    tables: dict[str, list[dict]] = Field(...)
    source: Literal['mysql_demo'] = 'mysql_demo'


@app.get('/health')
def health():
    return {'status': 'ok', 'version_informe': 3}


@app.get('/report')
def report():
    try:
        return read_report()
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post('/train')
def training(data: TrainingData):
    try:
        required = {'pedidos', 'pedido_detalles', 'suministros', 'clientes', 'stocks'}
        if not required.issubset(data.tables):
            raise ValueError('Faltan tablas para entrenar.')
        return train(data.tables, data.source)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
