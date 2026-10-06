# Smart Home — локальный запуск

Нужны Docker и Docker Compose. Команды выполняются из каталога `apps`.
Если Compose установлен как плагин, используйте `docker compose` вместо `docker-compose`.

```sh
docker-compose up --build -d --wait
```

Либо `./init.sh`. Запускаются три контейнера:

- `app` — исходный Go-монолит, http://localhost:8080;
- `temperature-api` — генератор температуры, http://localhost:8081;
- `postgres` — PostgreSQL 16 с постоянным томом `postgres_data`.

PostgreSQL запускает `smart_home/init.sql` при первой инициализации пустого тома.
Скрипт сам создаёт базу `smarthome`, поэтому `POSTGRES_DB=smarthome` не задаётся.
Монолит дожидается готовности базы и сервиса температуры.

## Проверка

```sh
curl 'http://localhost:8081/temperature?location=Living%20Room'
curl 'http://localhost:8081/temperature?location=Living%20Room'
curl 'http://localhost:8081/temperature?sensor_id=1'
curl http://localhost:8081/temperature/1
curl http://localhost:8080/health

curl -X POST http://localhost:8080/api/v1/sensors \
  -H 'Content-Type: application/json' \
  -d '{"name":"Living Room Temperature","type":"temperature","location":"Living Room","unit":"°C"}'

curl http://localhost:8080/api/v1/sensors
curl http://localhost:8080/api/v1/sensors
```

Новые случайные показания доступны через `GET /temperature?location=...`
и `GET /temperature/{sensor_id}` на порту 8081.
В Postman можно проверить `Create Sensor` и повторить `Get All Sensors` монолита.
Его код не изменён: при чтении датчиков он обращается к `/temperature/{id}`
и получает новые показания со статусом `active`.

Температура генерируется в диапазоне 15–30 °C. ID `1`, `2`, `3` соответствуют
`Living Room`, `Bedroom`, `Kitchen` в ответе сервиса. Query-параметры `location`
и `sensor_id` необязательны: отсутствующая локация определяется по ID,
отсутствующий ID — по локации. Для неизвестного ID локация — `Unknown`,
для неизвестной локации ID — `0`. Без обоих параметров возвращаются `Unknown`
и `0`; если переданы оба, их значения сохраняются.
В маршруте `/temperature/{sensor_id}` ID из пути имеет приоритет над query-параметром.

Сервис температуры — один Go-файл без сторонних библиотек. Для отдельного запуска
нужен Go 1.22 или новее:

```sh
cd temperature-api
go run .
```

Порт — 8081.

## Остановка

```sh
docker-compose logs -f
docker-compose down
```

Данные PostgreSQL сохраняются в томе после остановки.
