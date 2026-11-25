"""Pruebas de cronología, consistencia y contrato del servicio, sin MySQL ni sockets."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi import HTTPException
from sklearn.ensemble import RandomForestRegressor

import engine
from app import TrainingData, health, report as api_report, training


class PredictiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = json.loads((Path(__file__).resolve().parent.parent/'datos/simulados_2025/datos.json').read_text())
        cls.orders, cls.lines, cls.products, cls.clients, cls.daily = engine.prepare(cls.tables)

    def test_forecast_never_reads_future_observations(self):
        cutoff = pd.Timestamp('2025-10-31')
        x, y, dates = engine.demand_samples(self.daily)
        model = RandomForestRegressor(n_estimators=5, random_state=2025).fit(x[dates <= cutoff], y[dates <= cutoff])
        forecast = engine.forecast(model, self.daily, cutoff)
        changed = self.daily.copy()
        changed.loc[changed.index > cutoff] = 999999
        self.assertEqual(forecast, engine.forecast(model, changed, cutoff))

    def test_repurchase_features_ignore_future_and_training_labels_end_before_split(self):
        cutoff = pd.Timestamp('2025-10-31')
        customer = self.orders[self.orders['cliente_id'].eq(self.orders.iloc[0]['cliente_id'])]
        self.assertEqual(engine.client_row(customer, cutoff), engine.client_row(customer[customer['fecha'] <= cutoff], cutoff))
        samples = engine.client_samples(self.orders, self.daily.index.max())
        train = [r for r in samples if r['label_end'] <= cutoff]
        test = [r for r in samples if r['cutoff'] > cutoff]
        self.assertTrue(train and test)
        self.assertLess(max(r['label_end'] for r in train), min(r['cutoff'] for r in test))

    def test_invalid_quantities_are_rejected(self):
        broken = copy.deepcopy(self.tables)
        broken['pedido_detalles'][0]['cantidad'] = -10
        with self.assertRaises(ValueError):
            engine.prepare(broken)

    def test_timing_labels_are_actual_future_purchases_with_no_invented_censoring_dates(self):
        dates = pd.to_datetime(['2025-01-01', '2025-01-08', '2025-01-15', '2025-02-03'])
        orders = pd.DataFrame({'id': range(4), 'cliente_id': [1]*4, 'fecha': dates, 'total': [100.0]*4})
        samples = engine.client_samples(orders, pd.Timestamp('2025-04-01'))
        observed = samples[0]
        self.assertEqual(observed['cutoff'], pd.Timestamp('2025-01-31'))
        self.assertEqual(observed['dias_hasta_compra'], 3)
        self.assertTrue(any(r['dias_hasta_compra'] is None for r in samples))
        for sample in samples:
            if sample['dias_hasta_compra'] is not None:
                predicted_date = sample['cutoff'] + pd.Timedelta(days=sample['dias_hasta_compra'])
                self.assertIn(predicted_date, dates)
                self.assertGreater(predicted_date, sample['cutoff'])
                self.assertLessEqual(predicted_date, sample['label_end'])

    def test_timing_features_and_forecast_ignore_future_dates(self):
        class ThreeDays:
            def predict(self, _):
                return np.asarray([3.0])
        dates = pd.to_datetime(['2025-12-01', '2025-12-10', '2025-12-20', '2026-01-05'])
        history = pd.DataFrame({'fecha': dates, 'total': [100.0]*4})
        cutoff = pd.Timestamp('2025-12-31')
        known = history[history['fecha'] <= cutoff]
        self.assertEqual(engine.timing_row(history, cutoff), engine.timing_row(known, cutoff))
        result = engine.next_purchase(ThreeDays(), history, cutoff, .9)
        self.assertEqual(result['proxima_compra_dias'], 3)
        self.assertEqual(result['proxima_compra_fecha'], '2026-01-03')
        self.assertTrue(result['estimacion_disponible'])

    def test_no_date_for_sparse_inactive_low_probability_or_unavailable_model(self):
        class ThreeDays:
            def predict(self, _):
                return np.asarray([3.0])
        history = pd.DataFrame({'fecha': pd.to_datetime(['2025-09-01', '2025-09-10', '2025-09-20']), 'total': [100.0]*3})
        cases = [
            (ThreeDays(), history.iloc[:2], pd.Timestamp('2025-09-21'), .9),
            (ThreeDays(), history, pd.Timestamp('2025-12-31'), .9),
            (ThreeDays(), history, pd.Timestamp('2025-09-21'), .1),
            (None, history, pd.Timestamp('2025-09-21'), .9),
        ]
        for model, data, cutoff, probability in cases:
            result = engine.next_purchase(model, data, cutoff, probability)
            self.assertFalse(result['estimacion_disponible'])
            self.assertIsNone(result['proxima_compra_dias'])
            self.assertIsNone(result['proxima_compra_fecha'])
            self.assertTrue(result['motivo_estimacion'])

    def test_product_features_use_only_known_purchases(self):
        cutoff = pd.Timestamp('2025-12-31')
        dates = pd.to_datetime(['2025-11-01', '2025-12-20', '2025-12-29', '2026-01-05']).to_numpy()
        quantities = np.asarray([10., 20., 30., 99999.])
        customer = [2, 3, 8, 1000, 10.]
        row = engine.product_row((dates, quantities), cutoff, customer)
        self.assertEqual(row, engine.product_row((dates[:3], quantities[:3]), cutoff, customer))
        self.assertEqual(row[-1], 20.)
        self.assertIsNone(engine.product_row((dates[-1:], quantities[-1:]), cutoff, customer))

    def test_api_missing_report_invalid_payload_and_busy_training(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(engine, 'REPORT', Path(temp)/'missing.json'):
            self.assertEqual(health(), {'status': 'ok', 'version_informe': 3})
            with self.assertRaises(HTTPException) as missing:
                api_report()
            self.assertEqual(missing.exception.status_code, 404)
            with self.assertRaises(HTTPException) as invalid:
                training(TrainingData(tables={}))
            self.assertEqual(invalid.exception.status_code, 422)
            engine.LOCK.acquire()
            try:
                with self.assertRaises(HTTPException) as busy:
                    training(TrainingData(tables=self.tables))
                self.assertEqual(busy.exception.status_code, 409)
            finally:
                engine.LOCK.release()

    def test_full_training_contract(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(engine, 'REPORT', Path(temp)/'report.json'):
            report = training(TrainingData(tables=self.tables, source='mysql_demo'))
            self.assertEqual(report['resumen']['pedidos'], 933)
            self.assertEqual(report['pronostico_desde'], '2026-01-01')
            self.assertEqual(report['pronostico_hasta'], '2026-01-30')
            self.assertEqual(report['fuente'], 'mysql_demo')
            for product in report['productos']:
                self.assertEqual(product['sugerencia_compra'], max(0, product['pronostico_30']-product['stock']))
                self.assertTrue(all(np.isfinite(product[k]) for k in ['pronostico_30', 'stock', 'modelo_30', 'referencia_30']))
            self.assertTrue(all(0 <= c['probabilidad'] <= 100 for c in report['clientes']))
            self.assertTrue(report['tiempo_recompra']['disponible'])
            self.assertGreaterEqual(report['tiempo_recompra']['mae_dias'], 0)
            self.assertTrue(any(c['estimacion_disponible'] for c in report['clientes']))
            for customer in report['clientes']:
                if customer['estimacion_disponible']:
                    self.assertTrue(1 <= customer['proxima_compra_dias'] <= 30)
                    self.assertEqual(customer['proxima_compra_fecha'], str((pd.Timestamp(report['hasta'])+pd.Timedelta(days=customer['proxima_compra_dias'])).date()))
                else:
                    self.assertIsNone(customer['proxima_compra_fecha'])
            self.assertTrue(report['productos_cliente']['disponible'])
            self.assertEqual(report['version_informe'], 3)
            known = {(int(row['cliente_id']), int(row['suministro_id'])) for row in self.lines.to_dict('records')}
            for customer in report['clientes']:
                for product in customer['productos']:
                    self.assertIn((customer['id'], product['suministro_id']), known)
                    self.assertTrue(0 <= product['probabilidad_producto'] <= 100)
                    self.assertGreaterEqual(product['cantidad_habitual'], 1)
            self.assertTrue((Path(temp)/'models.joblib').exists())
            self.assertEqual(api_report(), report)


if __name__ == '__main__':
    unittest.main()
