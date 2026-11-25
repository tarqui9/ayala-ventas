"""Modelos sencillos, evaluados por fecha y separados de la aplicación comercial."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math
import os
import tempfile
import threading

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, precision_score, recall_score
import sklearn
from joblib import dump

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / 'artifacts' / 'report.json'
LOCK = threading.Lock()
DEMAND_FEATURES = ['producto', 'dia_semana', 'mes_seno', 'mes_coseno', 'media_7', 'media_28', 'mismo_dia_4_semanas']
CLIENT_FEATURES = ['dias_sin_compra', 'pedidos_30', 'pedidos_90', 'gasto_90', 'intervalo_medio']
TIMING_FEATURES = CLIENT_FEATURES + ['ultimo_intervalo', 'variacion_intervalos', 'fechas_compra', 'dia_semana']
PRODUCT_FEATURES = CLIENT_FEATURES + ['dias_desde_producto', 'pedidos_producto_30', 'pedidos_producto_90', 'proporcion_pedidos_producto', 'intervalo_producto', 'cantidad_media_reciente']


def prepare(tables):
    orders = pd.DataFrame(tables['pedidos'])
    details = pd.DataFrame(tables['pedido_detalles'])
    products = pd.DataFrame(tables['suministros'])
    clients = pd.DataFrame(tables['clientes'])
    if orders.empty or details.empty or products.empty or clients.empty:
        raise ValueError('No hay suficientes pedidos, productos o clientes para entrenar.')
    for frame in [orders, details, products, clients]:
        if frame['id'].duplicated().any():
            raise ValueError('Hay identificadores duplicados.')
    orders = orders[orders['estado'].eq('ENTREGADO')].copy()
    orders['fecha'] = pd.to_datetime(orders['fecha'], errors='raise').dt.normalize()
    for frame, columns in [(orders, ['total']), (details, ['cantidad', 'total'])]:
        for column in columns:
            frame[column] = pd.to_numeric(frame[column], errors='raise')
            if not np.isfinite(frame[column]).all() or (frame[column] < 0).any():
                raise ValueError('Importes o cantidades inválidos.')
    if orders.empty:
        raise ValueError('Se necesitan ventas entregadas.')
    if not set(orders['cliente_id']).issubset(set(clients['id'])):
        raise ValueError('Hay pedidos sin cliente válido.')
    if not set(details['pedido_id']).issubset(set(pd.DataFrame(tables['pedidos'])['id'])):
        raise ValueError('Hay detalles sin pedido válido.')
    if not set(details['suministro_id']).issubset(set(products['id'])):
        raise ValueError('Hay detalles sin producto válido.')
    lines = details.merge(orders[['id', 'fecha', 'cliente_id']], left_on='pedido_id', right_on='id', suffixes=('', '_pedido'), validate='many_to_one')
    start, end = orders['fecha'].min(), orders['fecha'].max()
    if (end - start).days < 300:
        raise ValueError('Para esta demostración se requieren al menos 300 días de historial.')
    dates = pd.date_range(start, end, freq='D')
    daily = lines.pivot_table(index='fecha', columns='suministro_id', values='cantidad', aggfunc='sum', fill_value=0).reindex(index=dates, columns=products['id'], fill_value=0).fillna(0).astype(float)
    return orders, lines, products, clients, daily


def demand_features(pid, day, history):
    values = np.asarray(history, dtype=float)
    return [pid, day.day_of_week, math.sin(2*math.pi*day.month/12), math.cos(2*math.pi*day.month/12), float(values[-7:].mean()), float(values[-28:].mean()), float(values[-7::-7][:4].mean())]


def demand_samples(daily):
    x, y, dates = [], [], []
    for pid in daily.columns:
        history = daily[pid].to_numpy()
        for i in range(28, len(history)):
            x.append(demand_features(pid, daily.index[i], history[:i]))
            y.append(history[i])
            dates.append(daily.index[i])
    return np.asarray(x), np.asarray(y), pd.DatetimeIndex(dates)


def forecast(model, daily, cutoff, horizon=30):
    """Pronóstico recursivo: nunca usa ventas observadas posteriores al corte."""
    histories = {pid: daily.loc[:cutoff, pid].tolist() for pid in daily.columns}
    totals = {pid: 0.0 for pid in daily.columns}
    for day in pd.date_range(cutoff + pd.Timedelta(days=1), periods=horizon):
        predictions = model.predict(np.asarray([demand_features(pid, day, histories[pid]) for pid in daily.columns]))
        for pid, pred in zip(daily.columns, predictions):
            value = max(0.0, float(pred))
            histories[pid].append(value)
            totals[pid] += value
    return totals


def client_row(history, cutoff):
    h = history[history['fecha'] <= cutoff].sort_values('fecha')
    if h.empty:
        return None
    recent = h[h['fecha'] > cutoff - pd.Timedelta(days=90)]
    intervals = h['fecha'].drop_duplicates().diff().dt.days.dropna()
    return [int((cutoff-h['fecha'].max()).days), int((h['fecha'] > cutoff-pd.Timedelta(days=30)).sum()), len(recent), float(recent['total'].sum()), float(intervals.mean()) if len(intervals) else 30.0]


def client_samples(orders, end):
    rows = []
    first = orders['fecha'].min() + pd.Timedelta(days=30)
    for cutoff in pd.date_range(first, end-pd.Timedelta(days=30), freq='14D'):
        for cid, history in orders.groupby('cliente_id'):
            features = client_row(history, cutoff)
            if features is None:
                continue
            future = history.loc[(history['fecha'] > cutoff) & (history['fecha'] <= cutoff+pd.Timedelta(days=30)), 'fecha']
            next_date = future.min() if len(future) else None
            rows.append({'cliente_id': int(cid), 'cutoff': cutoff, 'label_end': cutoff+pd.Timedelta(days=30),
                         'x': features, 'y': int(next_date is not None),
                         'x_tiempo': timing_row(history, cutoff),
                         'dias_hasta_compra': int((next_date-cutoff).days) if next_date is not None else None})
    return rows


def timing_row(history, cutoff):
    """Solo información conocida al corte; se requieren tres fechas distintas."""
    dates = history.loc[history['fecha'] <= cutoff, 'fecha'].drop_duplicates().sort_values()
    if len(dates) < 3:
        return None
    gaps = dates.diff().dt.days.dropna()
    return client_row(history, cutoff) + [float(gaps.iloc[-1]), float(gaps.std(ddof=0)), len(dates), cutoff.day_of_week]


def train_timing(samples, split):
    # Sin compra observada en 30 días no hay una fecha objetivo conocida.
    # Esos casos pertenecen al clasificador; nunca se les inventa un plazo.
    observed = [r for r in samples if r['dias_hasta_compra'] is not None and r['x_tiempo'] is not None]
    training = [r for r in observed if r['label_end'] <= split]
    testing = [r for r in observed if r['cutoff'] > split]
    metrics = {'algoritmo': 'RandomForestRegressor', 'variables': TIMING_FEATURES,
               'horizonte_dias': 30, 'minimo_fechas_compra': 3,
               'muestras_entrenamiento': len(training), 'muestras_prueba': len(testing),
               'casos_sin_fecha_observada': sum(r['dias_hasta_compra'] is None for r in samples),
               'mae_dias': None, 'mae_referencia_dias': None,
               'modelo_supera_referencia': None, 'disponible': False,
               'evaluacion': 'Solo casos con recompra observada dentro de 30 días y al menos tres fechas de compra previas.'}
    if len(training) < 30 or len(testing) < 10:
        return None, metrics
    model = RandomForestRegressor(n_estimators=100, max_depth=8, min_samples_leaf=4, random_state=2025, n_jobs=1)
    model.fit(np.asarray([r['x_tiempo'] for r in training]), np.asarray([r['dias_hasta_compra'] for r in training]))
    actual = np.asarray([r['dias_hasta_compra'] for r in testing])
    predicted = model.predict(np.asarray([r['x_tiempo'] for r in testing]))
    baseline = [max(1.0, min(30.0, r['x'][4]-r['x'][0])) for r in testing]
    mae = float(mean_absolute_error(actual, predicted))
    mae_base = float(mean_absolute_error(actual, baseline))
    metrics.update(mae_dias=round(mae, 2), mae_referencia_dias=round(mae_base, 2),
                   modelo_supera_referencia=bool(mae < mae_base), disponible=True)
    model.fit(np.asarray([r['x_tiempo'] for r in observed]), np.asarray([r['dias_hasta_compra'] for r in observed]))
    return model, metrics


def next_purchase(model, history, cutoff, probability):
    result = {'proxima_compra_dias': None, 'proxima_compra_fecha': None,
              'estimacion_disponible': False, 'motivo_estimacion': ''}
    features = timing_row(history, cutoff)
    if features is None:
        result['motivo_estimacion'] = 'Historial insuficiente: se necesitan tres fechas de compra.'
    elif probability < .4:
        result['motivo_estimacion'] = 'Baja probabilidad de recompra en los próximos 30 días.'
    elif features[0] > max(45, 2*features[4]):
        result['motivo_estimacion'] = 'Inactividad prolongada respecto de su frecuencia habitual.'
    elif model is None:
        result['motivo_estimacion'] = 'Faltan ejemplos para entrenar y evaluar el plazo de recompra.'
    else:
        predicted = float(model.predict(np.asarray([features]))[0])
        if not math.isfinite(predicted) or not 0 < predicted <= 30:
            result['motivo_estimacion'] = 'El plazo estimado está fuera del horizonte de 30 días.'
        else:
            days = max(1, int(math.floor(predicted+.5)))
            result.update(proxima_compra_dias=days,
                          proxima_compra_fecha=str((cutoff+pd.Timedelta(days=days)).date()),
                          estimacion_disponible=True)
    return result


def product_history(lines):
    """Una fila por fecha y producto; no duplica un artículo repetido el mismo día."""
    result = {}
    for (cid, pid), group in lines.groupby(['cliente_id', 'suministro_id']):
        daily = group.groupby('fecha')['cantidad'].sum().sort_index()
        result[(int(cid), int(pid))] = (daily.index.to_numpy(), daily.to_numpy(dtype=float))
    return result


def product_row(history, cutoff, customer_features):
    dates, quantities = history
    index = int(np.searchsorted(dates, cutoff.to_datetime64(), side='right'))
    if index == 0:
        return None
    known, qty = dates[:index], quantities[:index]
    ages = (cutoff.to_datetime64()-known)/np.timedelta64(1, 'D')
    gaps = np.diff(known)/np.timedelta64(1, 'D')
    return customer_features + [float(ages[-1]), int((ages < 30).sum()), int((ages < 90).sum()),
                               min(1.0, int((ages < 90).sum())/max(1, customer_features[2])),
                               float(gaps.mean()) if len(gaps) else 30.0, float(qty[-3:].mean())]


def train_products(samples, lines, split):
    histories = product_history(lines)
    by_client = {}
    for cid, pid in histories:
        by_client.setdefault(cid, []).append(pid)
    purchase_dates = {int(cid): group['fecha'].drop_duplicates().sort_values().to_numpy()
                      for cid, group in lines.groupby('cliente_id')}
    rows = []
    for sample in samples:
        if not sample['y']:
            continue  # Probabilidad condicionada a una recompra observada dentro de 30 días.
        cid, cutoff = sample['cliente_id'], sample['cutoff']
        dates = purchase_dates[cid]
        next_date = dates[np.searchsorted(dates, cutoff.to_datetime64(), side='right')]
        for pid in by_client.get(cid, []):
            history = histories[(cid, pid)]
            features = product_row(history, cutoff, sample['x'])
            if features is None:
                continue  # No recomendar productos que aún no había comprado al corte.
            rows.append({'x': features, 'y': int(next_date in history[0]),
                         'cutoff': cutoff, 'label_end': sample['label_end']})
    training = [r for r in rows if r['label_end'] <= split]
    testing = [r for r in rows if r['cutoff'] > split]
    metrics = {'algoritmo': 'RandomForestClassifier', 'variables': PRODUCT_FEATURES,
               'disponible': False, 'muestras_entrenamiento': len(training),
               'muestras_prueba': len(testing), 'f1_macro': None, 'f1_macro_referencia': None,
               'definicion': 'Inclusión del producto en la próxima fecha de compra, condicionada a que el cliente vuelva dentro de 30 días.'}
    if len(training) < 50 or len(testing) < 10 or len({r['y'] for r in training}) < 2:
        return None, metrics, histories
    model = RandomForestClassifier(n_estimators=80, max_depth=8, min_samples_leaf=8,
                                   class_weight='balanced', random_state=2025, n_jobs=1)
    x, y = np.asarray([r['x'] for r in training]), np.asarray([r['y'] for r in training])
    model.fit(x, y)
    actual = np.asarray([r['y'] for r in testing])
    predicted = model.predict(np.asarray([r['x'] for r in testing]))
    baseline = np.full(len(actual), int(y.mean() >= .5))
    metrics.update(disponible=True, f1_macro=round(float(f1_score(actual, predicted, average='macro', zero_division=0)), 4),
                   f1_macro_referencia=round(float(f1_score(actual, baseline, average='macro', zero_division=0)), 4))
    model.fit(np.asarray([r['x'] for r in rows]), np.asarray([r['y'] for r in rows]))
    return model, metrics, histories


def customer_products(model, histories, cid, cutoff, features, products):
    results = []
    for product in products:
        pid = int(product['id'])
        history = histories.get((int(cid), pid))
        if history is None:
            continue
        row = product_row(history, cutoff, features)
        if row is None:
            continue
        dates, quantities = history
        known = dates <= cutoff.to_datetime64()
        quantity = max(1, int(math.floor(float(np.median(quantities[known][-3:]))+.5)))
        probability = None if model is None else round(float(model.predict_proba(np.asarray([row]))[0, list(model.classes_).index(1)])*100, 1)
        results.append({'suministro_id': pid, 'nombre': product['nombre'],
                        'probabilidad_producto': probability, 'cantidad_habitual': quantity,
                        'unidades_historicas': int(quantities[known].sum()),
                        'fechas_compra': int(known.sum()),
                        'ultima_compra': str(pd.Timestamp(dates[known][-1]).date())})
    return sorted(results, key=lambda r: (-(r['probabilidad_producto'] if r['probabilidad_producto'] is not None else -1), -r['unidades_historicas']))


def train(tables, source='archivo_simulado'):
    if not LOCK.acquire(blocking=False):
        raise RuntimeError('Ya hay un entrenamiento en curso. Espera a que termine.')
    try:
        return _train(tables, source)
    finally:
        LOCK.release()


def _train(tables, source):
    orders, lines, products, clients, daily = prepare(tables)
    end = daily.index.max()
    # Últimos 60 días reservados. Las etiquetas de recompra deben terminar antes del corte.
    split = end-pd.Timedelta(days=60)
    x, y, dates = demand_samples(daily)
    reg = RandomForestRegressor(n_estimators=80, min_samples_leaf=5, random_state=2025, n_jobs=1)
    reg.fit(x[dates <= split], y[dates <= split])
    actuals, estimates, baselines, comparisons = [], [], [], []
    for cutoff in [split, split+pd.Timedelta(days=30)]:
        predicted = forecast(reg, daily, cutoff)
        actual = daily.loc[cutoff+pd.Timedelta(days=1):cutoff+pd.Timedelta(days=30)].sum()
        baseline = daily.loc[:cutoff].tail(28).mean()*30
        for pid in daily.columns:
            actuals.append(float(actual[pid]))
            estimates.append(predicted[pid])
            baselines.append(float(baseline[pid]))
            comparisons.append({'producto_id': int(pid), 'desde': str((cutoff+pd.Timedelta(days=1)).date()), 'real': round(float(actual[pid]), 1), 'modelo': round(predicted[pid], 1), 'referencia': round(float(baseline[pid]), 1)})
    mae_model = float(mean_absolute_error(actuals, estimates))
    mae_base = float(mean_absolute_error(actuals, baselines))
    use_model = mae_model < mae_base
    reg.fit(x, y)  # Solo después de medir, reentrenar con todo el historial.
    future = forecast(reg, daily, end)
    baseline_future = daily.tail(28).mean()*30

    samples = client_samples(orders, end)
    train_rows = [r for r in samples if r['label_end'] <= split]
    test_rows = [r for r in samples if r['cutoff'] > split]
    if not train_rows or not test_rows:
        raise ValueError('Faltan muestras cronológicas para probar la recompra.')
    cx = np.asarray([r['x'] for r in train_rows])
    cy = np.asarray([r['y'] for r in train_rows])
    if len(np.unique(cy)) < 2:
        raise ValueError('Para entrenar recompra hacen falta ejemplos de clientes que compran y que no compran en 30 días.')
    classifier = RandomForestClassifier(n_estimators=100, max_depth=6, min_samples_leaf=4, class_weight='balanced', random_state=2025, n_jobs=1)
    classifier.fit(cx, cy)
    tx, ty = np.asarray([r['x'] for r in test_rows]), np.asarray([r['y'] for r in test_rows])
    predicted = classifier.predict(tx)
    majority = int(np.mean(cy) >= .5)
    simple = np.full(len(ty), majority)
    metrics_client = {'accuracy': round(float(accuracy_score(ty, predicted)), 4), 'precision': round(float(precision_score(ty, predicted, zero_division=0)), 4), 'recall': round(float(recall_score(ty, predicted, zero_division=0)), 4), 'f1': round(float(f1_score(ty, predicted, zero_division=0)), 4), 'f1_referencia': round(float(f1_score(ty, simple, zero_division=0)), 4), 'f1_macro': round(float(f1_score(ty, predicted, average='macro', zero_division=0)), 4), 'f1_macro_referencia': round(float(f1_score(ty, simple, average='macro', zero_division=0)), 4), 'muestras_entrenamiento': len(train_rows), 'muestras_prueba': len(test_rows), 'positivos_prueba': int(ty.sum()), 'negativos_prueba': int((ty == 0).sum())}
    classifier.fit(np.asarray([r['x'] for r in samples]), np.asarray([r['y'] for r in samples]))
    timing_model, timing_metrics = train_timing(samples, split)
    product_model, product_metrics, histories = train_products(samples, lines, split)
    customer_results = []
    for client in clients.to_dict('records'):
        history = orders[orders['cliente_id'].eq(client['id'])]
        features = client_row(history, end)
        if features is None:
            continue
        prob = float(classifier.predict_proba(np.asarray([features]))[0, list(classifier.classes_).index(1)])
        purchases = lines[lines['cliente_id'].eq(client['id'])].groupby('suministro_id')['cantidad'].sum()
        favorite_id = int(purchases.idxmax()) if len(purchases) else None
        favorite = products.loc[products['id'].eq(favorite_id), 'nombre']
        customer_results.append({'id': int(client['id']), 'nombre': client['razon_social'], 'probabilidad': round(prob*100, 1), 'nivel': 'Alta' if prob >= .7 else ('Media' if prob >= .4 else 'Baja'), 'dias_sin_compra': features[0], 'pedidos_30': features[1], 'intervalo_dias': round(features[4], 1), 'producto_preferido': str(favorite.iloc[0]) if len(favorite) else 'Sin historial'})
        customer_results[-1].update(next_purchase(timing_model, history, end, prob))
        customer_results[-1]['productos'] = customer_products(product_model, histories, client['id'], end, features, products.to_dict('records'))
    stock = pd.DataFrame(tables.get('stocks', []))
    product_results = []
    for p in products.to_dict('records'):
        pid = p['id']
        sales = lines[lines['suministro_id'].eq(pid)]
        per_month = sales.groupby(sales['fecha'].dt.month)['cantidad'].sum()
        units = int(math.ceil(future[pid] if use_model else baseline_future[pid]))
        current_stock = int(stock.loc[stock['suministro_id'].eq(pid), 'cantidad'].sum()) if not stock.empty else 0
        product_results.append({'id': int(pid), 'nombre': p['nombre'], 'unidades_vendidas': int(sales['cantidad'].sum()), 'ventas_bs': round(float(sales['total'].sum()), 2), 'mes_mayor_venta': int(per_month.idxmax()) if len(per_month) else None, 'stock': current_stock, 'pronostico_30': units, 'modelo_30': int(math.ceil(future[pid])), 'referencia_30': int(math.ceil(baseline_future[pid])), 'sugerencia_compra': max(0, units-current_stock), 'historial_mensual': [{'mes': str(month), 'unidades': int(daily.loc[(daily.index.to_period('M') == month), pid].sum())} for month in daily.index.to_period('M').unique()]})
    monthly = orders.groupby(orders['fecha'].dt.to_period('M')).agg(ventas_bs=('total', 'sum'), pedidos=('id', 'count'))
    report = {'entrenado_en': datetime.now(timezone.utc).isoformat(), 'fuente': source, 'datos_simulados': True, 'huella_datos': hashlib.sha256(json.dumps(tables, sort_keys=True, default=str).encode()).hexdigest(), 'versiones': {'scikit_learn': sklearn.__version__, 'pandas': pd.__version__, 'numpy': np.__version__}, 'desde': str(daily.index.min().date()), 'hasta': str(end.date()), 'pronostico_desde': str((end+pd.Timedelta(days=1)).date()), 'pronostico_hasta': str((end+pd.Timedelta(days=30)).date()), 'corte_entrenamiento': str(split.date()), 'resumen': {'productos': len(products), 'clientes': len(clients), 'pedidos': len(orders), 'ventas_bs': round(float(orders['total'].sum()), 2)}, 'demanda': {'algoritmo': 'RandomForestRegressor', 'metodo_utilizado': 'Random Forest' if use_model else 'Promedio de los últimos 28 días', 'mae_modelo': round(mae_model, 2), 'mae_referencia': round(mae_base, 2), 'unidad_error': 'unidades por producto en 30 días', 'modelo_supera_referencia': bool(use_model), 'variables': DEMAND_FEATURES, 'comparaciones': comparisons}, 'recompra': {'algoritmo': 'RandomForestClassifier', 'variables': CLIENT_FEATURES, **metrics_client}, 'mensual': [{'mes': str(month), 'ventas_bs': round(float(row['ventas_bs']), 2), 'pedidos': int(row['pedidos'])} for month, row in monthly.iterrows()], 'productos': sorted(product_results, key=lambda r: r['unidades_vendidas'], reverse=True), 'clientes': sorted(customer_results, key=lambda r: r['probabilidad'], reverse=True), 'notas': ['Datos simulados: resultados de demostración, no precisión validada en el negocio real.', 'El pronóstico empieza después de la última venta registrada, no después de la fecha de hoy.', 'La sugerencia de compra resta el stock al pronóstico, sin stock de seguridad ni plazo de entrega.', 'El mes con más ventas describe este historial; un año no confirma estacionalidad anual.', 'Las probabilidades de recompra son estimaciones del modelo, no garantías.']}
    report['version_informe'] = 3
    report['productos_cliente'] = product_metrics
    report['tiempo_recompra'] = timing_metrics
    report['notas'].append('La próxima compra es una fecha aproximada, condicionada a que el cliente vuelva dentro de 30 días. No se asigna fecha si hay poca historia, baja probabilidad o inactividad prolongada.')
    # Un solo archivo: el lector siempre ve una versión completa incluso si falla un entrenamiento.
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    model_fd, model_path = tempfile.mkstemp(dir=REPORT.parent, suffix='.joblib')
    os.close(model_fd)
    try:
        dump({'demanda': reg, 'recompra': classifier, 'tiempo_recompra': timing_model,
              'productos_cliente': product_model, 'variables_productos_cliente': PRODUCT_FEATURES,
              'variables_tiempo_recompra': TIMING_FEATURES, 'variables_demanda': DEMAND_FEATURES,
              'variables_recompra': CLIENT_FEATURES, 'hasta': str(end.date()),
              'version_sklearn': sklearn.__version__}, model_path)
        os.replace(model_path, REPORT.parent / 'models.joblib')
    finally:
        if os.path.exists(model_path):
            os.unlink(model_path)
    fd, path = tempfile.mkstemp(dir=REPORT.parent, suffix='.json')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(report, stream, ensure_ascii=False, allow_nan=False)
        os.replace(path, REPORT)
    finally:
        if os.path.exists(path):
            os.unlink(path)
    return report


def read_report():
    if not REPORT.exists():
        raise FileNotFoundError('Todavía no hay entrenamiento. Pulsa Entrenar análisis.')
    return json.loads(REPORT.read_text())


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--datos', type=Path, default=ROOT.parent/'datos/simulados_2025/datos.json')
    parser.add_argument('--stdin', action='store_true', help='Recibe tablas de MySQL por entrada estándar y devuelve el informe completo.')
    args = parser.parse_args()
    if args.stdin:
        import sys
        payload = json.load(sys.stdin)
        if payload.get('source') != 'mysql_demo':
            raise ValueError('La entrada de Laravel debe indicar source=mysql_demo.')
        result = train(payload['tables'], source='mysql_demo')
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    else:
        result = train(json.loads(args.datos.read_text()))
        print(json.dumps({k: result[k] for k in ['desde', 'hasta', 'resumen', 'demanda', 'recompra', 'productos_cliente']}, ensure_ascii=False, indent=2))
