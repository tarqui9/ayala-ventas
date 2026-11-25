"""Ejemplo de pruebas unitarias para la defensa: cada prueba revisa UNA función."""
import unittest

import pandas as pd

import engine


class EjemploPruebasUnitarias(unittest.TestCase):

    def test_calcula_promedios_de_ventas_recientes(self):
        # 1. PREPARAR: 21 días vendiendo 2 unidades y los últimos 7 días vendiendo 9
        historial = [2] * 21 + [9] * 7
        dia = pd.Timestamp('2025-03-10')  # es lunes

        # 2. EJECUTAR: la función que arma las variables del modelo de demanda
        variables = engine.demand_features(5, dia, historial)

        # 3. VERIFICAR: comparar con lo que debería salir
        self.assertEqual(variables[0], 5)       # identificador del producto
        self.assertEqual(variables[1], 0)       # lunes = 0
        self.assertEqual(variables[4], 9.0)     # promedio de los últimos 7 días
        self.assertEqual(variables[5], 3.75)    # promedio de 28 días: (21*2 + 7*9) / 28

    def test_rechaza_identificadores_duplicados(self):
        # 1. PREPARAR: dos pedidos con el mismo id (dato corrupto)
        tablas = {
            'pedidos': [{'id': 1}, {'id': 1}],
            'pedido_detalles': [{'id': 1}],
            'suministros': [{'id': 1}],
            'clientes': [{'id': 1}],
        }
        # 2. EJECUTAR y 3. VERIFICAR: debe lanzar un error con ese mensaje
        with self.assertRaisesRegex(ValueError, 'duplicados'):
            engine.prepare(tablas)


if __name__ == '__main__':
    unittest.main()
