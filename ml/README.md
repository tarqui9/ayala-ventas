# Análisis predictivo básico de AYALA

Se añadieron tres pantallas al final del menú de Angular: Productos y demanda, Clientes y recompra, y Entrenamiento. La estructura comercial y las tablas existentes se conservan. Los datos de demostración se identifican con una sucursal propia, nombres DEMO y códigos DEMO25.

## Puesta en marcha

Desde la raíz del proyecto, una sola vez:

```bash
python3 -m venv ml/.venv
ml/.venv/bin/python -m pip install -r ml/requirements.txt
cd laravel_nventas-main
php artisan db:seed --class=Analisis2025Seeder
cd ..
```

MySQL debe estar encendido (`sudo systemctl start mysql`). El seeder usa la conexión existente de Laravel, inserta dentro de una transacción y no borra registros. No ejecutar DatabaseSeeder, migrate:fresh ni importar el SQL independiente encima de la aplicación. Si la sucursal DEMO ya existe, el cargador rechaza repetirla.

Para iniciar todo:

```bash
./levantar.sh
```

El script conserva servicios ya iniciados y arranca los que faltan. Dejar esa terminal abierta. En `http://127.0.0.1:4200`, iniciar sesión como siempre, abrir **Entrenamiento** al final del menú y pulsar **Entrenar análisis**. Se necesitan las librerías y el servicio Python iniciado; no es necesario usar Postman para manejar la aplicación.

Comprobar la carga y entrenar desde otra terminal:

```bash
cd laravel_nventas-main
php artisan analisis:estado
php artisan analisis:entrenar
```

Para iniciar solamente Python:

```bash
cd ml
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8001
```

No hace falta modificar `.env`. Si se cambia el puerto de Python, configurar `PREDICTIVO_URL` en Laravel y ejecutar `php artisan config:clear`.

## Cómo está hecho

```text
Angular + Chart.js → Laravel → FastAPI → pandas / NumPy / scikit-learn
                        ↓
                 MySQL, solo lectura
```

El cargador escribe los datos de demostración en MySQL una sola vez. Después, el servicio Laravel lee únicamente las ventas ENTREGADAS de `Sucursal DEMO 2025` y sus productos, clientes y stock. Envía esas tablas a FastAPI al entrenar. Python calcula las variables, evalúa los modelos, vuelve a entrenarlos con el historial disponible y guarda los resultados. Angular consulta a Laravel, que lee el informe de Python, y lo presenta en tablas y gráficos Chart.js. No se mezclan ventas reales y simuladas.

No se añadieron roles nuevos: las rutas API conservan `auth:api`, las pantallas utilizan el acceso existente a la aplicación y los tres enlaces no tienen restricciones adicionales de rol. El servicio Python se ejecuta en localhost.

## Qué aprende cada modelo

**Demanda — RandomForestRegressor:** estima ventas diarias por producto mediante número de producto, día de semana, mes expresado con seno y coseno, promedio de 7 y 28 días y promedio del mismo día de las cuatro semanas anteriores. El pronóstico de 30 días es recursivo: al avanzar agrega sus propias estimaciones, nunca ventas futuras observadas.

**Probabilidad de recompra — RandomForestClassifier:** estima si habrá al menos una compra en los siguientes 30 días. Usa días desde la última compra, pedidos de los últimos 30 y 90 días, gasto de los últimos 90 días e intervalo medio entre compras. Las muestras son fotografías del historial cada 14 días. No se usan nombres, identificación personal ni información futura como variables. Los porcentajes son estimaciones sin calibración específica; alta desde 70%, media desde 40%, baja por debajo de 40%.

**Tiempo hasta recompra — RandomForestRegressor:** estima días restantes hasta la siguiente compra, condicionados a que ocurra dentro de 30 días. Se aprovechan las fotografías de clientes cada 14 días, conservando solo aquellas con al menos tres fechas de compra previas y una compra futura observada en la ventana. El objetivo es la diferencia entre esa primera compra futura y el corte. Se añaden último intervalo, variación de intervalos, número de fechas de compra y día de semana a las variables del clasificador. Los casos sin recompra observada no reciben una fecha inventada: siguen representados en el clasificador.

La evaluación del plazo utiliza fechas reservadas y reporta MAE en días, comparado con `máximo(1, mínimo(30, intervalo medio - días sin compra))`. Ese error es condicional a los casos observados con historial suficiente; no representa a todos los clientes ni garantiza un margen individual. Tras evaluar se reentrena con todas las muestras observadas. La pantalla utiliza este modelo de plazo e informa si no supera la referencia.

No se asigna fecha con menos de tres fechas de compra, probabilidad de recompra menor de 40%, inactividad mayor que el máximo entre 45 días y dos intervalos medios, o falta de muestras para entrenar y evaluar (mínimo 30 de entrenamiento y 10 de prueba). Son criterios operativos, no un cálculo estadístico de confianza. La fecha se cuenta desde la última venta global del conjunto, no desde hoy. Las fechas no superan el horizonte de 30 días y siempre se muestran como aproximadas.

El ranking de productos, el mes con más ventas y el producto preferido de cada cliente son resúmenes históricos, no predicciones. La sugerencia de compra es una cuenta sencilla: máximo entre cero y pronóstico menos stock. No incluye reserva de seguridad, plazos de entrega, devoluciones ni vencimientos.

## Entrenamiento y prueba

El corte se coloca 60 días antes de la última fecha observada (1 de noviembre de 2025 para estos datos). La demanda se entrena hasta el corte incluido y se evalúa en dos ventanas posteriores de 30 días. Para la prueba de recompra, tanto las variables como el final de los 30 días de etiqueta del entrenamiento quedan antes o en el corte; las fotografías de prueba quedan después. Las ventanas que cruzan el corte se excluyen. No se mezclan fechas al azar.

Demanda se compara con mantener el promedio de los últimos 28 días durante los siguientes 30. El MAE mide unidades de error por producto y ventana de 30 días; menor es mejor. Si Random Forest no mejora esa referencia en la evaluación, el pronóstico mostrado utiliza el promedio histórico y la pantalla lo informa explícitamente.

Para recompra se informa exactitud, precisión, recall, F1 de la clase recompra y F1 macro. La pantalla enfatiza F1 macro porque existen muchos más ejemplos con recompra que sin ella. La referencia elige siempre la clase mayoritaria del entrenamiento. Las fotografías del mismo cliente en fechas distintas son observaciones relacionadas, no clientes independientes.

Una vez evaluados, los modelos se ajustan de nuevo con todas las muestras cuya respuesta ya se conoce. Los números de evaluación corresponden al ajuste anterior con fechas reservadas; no al ajuste final sobre todo el historial.

La predicción empieza después de la última venta del conjunto: del 1 al 30 de enero de 2026, aunque la fecha actual de la computadora sea posterior. Esto permite presentar el ejemplo sin confundir fechas.

## Archivos generados

- `artifacts/report.json`: tablas, pronósticos, métricas, periodo y fecha del entrenamiento. Incluye origen (`mysql_demo` o `archivo_simulado`) y huella de los datos.
- `artifacts/models.joblib`: modelos entrenados y nombres de variables. Solo se genera localmente; no cargar archivos joblib de terceros.
- `requirements-lock.txt`: versiones instaladas durante la comprobación. Puede usarse con pip para repetir ese entorno.
- `AYALA.postman_collection.json`: colección importable en Postman. Para las peticiones a Laravel, completar la variable `token` con un token de sesión válido; no contiene credenciales.

Para practicar sin MySQL:

```bash
ml/.venv/bin/python ml/engine.py
```

Esto entrena usando `datos/simulados_2025/datos.json`. El informe queda identificado como `archivo_simulado`, no como lectura de MySQL. Entrenar desde la aplicación lo reemplaza por el informe basado en MySQL.

## Comprobaciones

Desde la raíz:

```bash
ml/.venv/bin/python -m unittest discover -s ml -p 'test_*.py' -v
cd laravel_nventas-main
vendor/bin/phpunit --filter AnalisisPredictivoTest
cd '../app ang ventas'
PATH="$PWD/node_modules/.bin:$PATH" node_modules/.bin/ng build --configuration development
```

Las pruebas Python comprueban generación de resultados, cálculos de compra sugerida, rangos de probabilidad, rechazo de cantidades inválidas, separación temporal y que alterar ventas posteriores al corte no cambie las predicciones de ese corte. Prueban las funciones de FastAPI directamente sin abrir sockets. Las pruebas Laravel usan respuestas simuladas de Python y comprueban las rutas, el mensaje de informe inexistente y el entrenamiento; no prueban la conexión real a MySQL. Para comprobar el recorrido real, ejecutar `analisis:estado` y `analisis:entrenar` con los servicios iniciados, y consultar las tres pantallas.

## Para explicarlo en la defensa

“Laravel conserva las operaciones del negocio y obtiene los datos de MySQL. Angular presenta el análisis con Chart.js. Python limpia y agrupa los datos mediante pandas y NumPy. scikit-learn entrena cuatro modelos Random Forest: regresión de demanda, clasificación de recompra, regresión de días hasta la próxima compra y clasificación de productos del próximo pedido. FastAPI permite que Laravel se comunique con Python. Separé el entrenamiento y la prueba por fechas y comparé con referencias simples. Postman permite revisar las API”.

Los datos son simulados. Los resultados prueban el funcionamiento del proceso sobre este ejemplo, no la precisión con ventas reales. Un solo año tampoco permite confirmar un patrón estacional anual. Para uso comercial habría que evaluar con historial real y revisar cantidades, precios, promociones, faltantes y plazos de entrega.

## Actualizar una instalación que ya estaba iniciada

Python no se recarga automáticamente. Para utilizar el nuevo modelo de días de recompra, detener con Ctrl+C la terminal que ejecuta `./levantar.sh`, volver a ejecutar ese script y pulsar **Entrenar análisis**. Después abrir **Clientes y recompra** y consultar las columnas **Próxima compra estimada** y **Fecha aproximada**. No se necesita volver a cargar datos ni cambiar MySQL. Si el informe antiguo no contiene `tiempo_recompra`, la pantalla pide generar el nuevo entrenamiento.

## Sugerencias dentro del formulario de pedidos

Todos los vendedores que acceden al formulario habitual encuentran debajo **Ver sugerencias**. Al seleccionar un cliente, el botón consulta sus productos, predicciones disponibles, precios y stock actuales. **Añadir al pedido** incorpora una fila editable al formulario, sin guardar ni duplicar un artículo que ya esté incluido. **Añadir productos más probables** añade hasta cinco artículos disponibles con probabilidad desde 50%. Guardar continúa utilizando el flujo habitual de Pedidos.

El cuarto modelo, RandomForestClassifier, aprende si un producto previamente comprado aparece en la siguiente fecha de compra del cliente, condicionado a que exista una recompra dentro de 30 días. Las fotografías temporales y etiquetas se separan por el mismo corte del resto del análisis. No se utilizan artículos que el cliente aún no conocía al corte. El porcentaje es una estimación sin calibración y no se obtiene copiando el porcentaje general del cliente. El informe incluye F1 macro y referencia. Las cantidades propuestas son la mediana de las tres compras más recientes, no una predicción de cantidades. Sin historial o predicciones disponibles se indica claramente, sin porcentajes inventados.

En **Clientes y recompra** también se puede consultar un cliente, preparar una propuesta, ajustar cantidades y selección, y guardarla como un pedido pendiente real. Es editable en la misma pantalla mientras no esté preparado, pagado o despachado. La creación utiliza una clave para evitar duplicados por reintentos, valida precios y formas de pago en MySQL y considera reservas de otros pedidos de la misma sucursal. Guardar no registra pagos ni salidas de inventario; conserva el flujo de despacho existente. Los pedidos nuevos se registran con la fecha actual, aunque las predicciones se refieran al cierre histórico de 2025. Solo las ventas ENTREGADAS participan en el entrenamiento.

Se corrigió la selección de sucursal del pedido habitual para usar la del cliente (y la del pedido al editar), y la disponibilidad de productos considera la sucursal consultada. No se modificaron tablas ni se añadieron roles.

Pruebas de rutas y validaciones:

```bash
cd laravel_nventas-main
vendor/bin/phpunit --filter 'AnalisisPredictivoTest|PedidoPredictivoTest'
```

Prueba adicional contra MySQL, con los datos DEMO cargados y MySQL disponible:

```bash
PREDICTIVE_DB_TEST=1 vendor/bin/phpunit --filter PedidoPredictivoDatabaseTest
```

Esta última comprueba creación real, edición, reservas, reintentos sin duplicados y rechazo de cantidades superiores al stock. Se ejecuta dentro de transacciones que se revierten al terminar; no conserva pedidos de prueba. No se pudo ejecutar desde el entorno restringido del agente, que bloquea conexiones locales. No requiere Python porque prueba directamente el guardado en MySQL.

## Informe antiguo sin porcentajes por producto

Un informe con `version_informe` inferior a 3 no contiene el cuarto modelo. El servicio comprueba la versión del proceso Python: si todavía ejecuta una versión anterior, el entrenamiento utiliza automáticamente el motor actual por un proceso local, con las tablas leídas de MySQL. Para ejecutarlo explícitamente sin depender de reiniciar FastAPI:

```bash
cd laravel_nventas-main
php artisan analisis:entrenar --local
```

Esto no inserta ni modifica registros comerciales: lee MySQL y regenera los archivos de modelos y resultados. Al terminar informa cuántos clientes tienen porcentajes por producto. El botón de entrenamiento mantiene la ruta de FastAPI cuando ese servicio ya está actualizado. Un cliente ajeno al conjunto DEMO puede tener historial visible sin estar incluido en el modelo; ese caso tiene un mensaje diferente al de informe antiguo.
