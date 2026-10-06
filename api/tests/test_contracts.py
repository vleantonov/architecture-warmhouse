"""Run with: python -m unittest discover -s api/tests -v."""

import unittest
from copy import deepcopy
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate


API_DIR = Path(__file__).resolve().parents[1]
DEVICE_ID = "00000003-0000-4000-8000-000000000001"
MODEL_ID = "00000004-0000-4000-8000-000000000001"
COMMAND_ID = "00000007-0000-4000-8000-000000000001"
TIMESTAMP = "2026-10-05T09:00:00Z"
ERROR = {"code": "DEVICE_TIMEOUT", "message": "Timeout", "request_id": DEVICE_ID}


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = {
            path.name.split(".")[0]: yaml.safe_load(path.read_text())
            for path in API_DIR.glob("*.openapi.yaml")
        }

    def validator(self, service, schema):
        return Draft202012Validator(
            {"components": self.documents[service]["components"], **schema},
            format_checker=FormatChecker(),
        )

    def assert_contract(self, service, schema, payload, valid):
        validator = self.validator(service, {"$ref": f"#/components/schemas/{schema}"})
        errors = list(validator.iter_errors(payload))
        self.assertEqual(not errors, valid, f"{payload}: {[e.message for e in errors]}")

    def test_openapi_and_documented_examples(self):
        def walk(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        self.assertEqual(len(self.documents), 4)
        for service, document in self.documents.items():
            with self.subTest(service=service):
                validate(document)
                for node in walk(document):
                    if "schema" not in node:
                        continue
                    examples = [node["example"]] if "example" in node else []
                    examples.extend(
                        example["value"]
                        for example in node.get("examples", {}).values()
                        if "value" in example
                    )
                    for example in examples:
                        self.validator(service, node["schema"]).validate(example)

    def test_command_contracts_agree(self):
        cases = [
            ("target_temperature", 22, True),
            ("target_temperature", 22.5, True),
            ("power", "on", True),
            ("power", "off", True),
            ("lock_state", "locked", True),
            ("lock_state", "unlocked", True),
            ("target_temperature", "22", False),
            ("target_temperature", True, False),
            ("power", True, False),
            ("power", "locked", False),
            ("lock_state", "on", False),
            ("temperature", 22, False),
            ("", 22, False),
            ("power", None, False),
            ("power", {"unexpected": True}, False),
            ("power", ["on"], False),
        ]
        for service in ("parameters", "scenarios"):
            for parameter, value, valid in cases:
                with self.subTest(service=service, parameter=parameter, value=value):
                    command = {"device_id": DEVICE_ID, "parameter": parameter, "value": value}
                    if service == "scenarios":
                        command["id"] = COMMAND_ID
                    self.assert_contract(service, "command", command, valid)
            # Temperature commands keep an implicit Celsius value, regardless of
            # the device's selected reading unit; the request accepts no unit field.
            for unit in ("Cel", "[degF]", "K", None):
                with self.subTest(service=service, command_unit=unit):
                    command = {
                        "device_id": DEVICE_ID,
                        "parameter": "target_temperature",
                        "value": 22,
                        "unit": unit,
                    }
                    if service == "scenarios":
                        command["id"] = COMMAND_ID
                    self.assert_contract(service, "command", command, False)

    def test_partial_device_update(self):
        cases = [
            ({"name": "Камера у входа"}, True),
            ({"location": "Entrance"}, True),
            ({"name": "Камера", "location": "Entrance"}, True),
            ({"model_id": MODEL_ID}, True),
            ({"attributes": {}}, True),
            ({"attributes": {"video_endpoint": "https://partner.example/cameras/1/video"}}, True),
            ({"model_id": MODEL_ID, "measurement_unit": "[degF]", "attributes": {}}, True),
            ({}, False),
            ({"name": None}, False),
            ({"location": None}, False),
            ({"name": ""}, False),
            ({"location": ""}, False),
            ({"name": "Камера", "owner_id": DEVICE_ID}, False),
            ({"name": "Камера", "location": None}, False),
            ({"model_id": "not-a-uuid"}, False),
            ({"model_id": None}, False),
            ({"device_type": "THERMOSTAT"}, False),
            ({"owner_id": DEVICE_ID}, False),
            ({"id": DEVICE_ID}, False),
            ({"attributes": None}, False),
            ({"attributes": []}, False),
            ({"attributes": "{}"}, False),
            ({"attributes": {"video_endpoint": None}}, False),
        ]
        # Model-dependent compatibility is checked by the service after merging
        # this partial request, rather than by its standalone JSON Schema.
        for unit, valid in (("Cel", True), ("[degF]", True), ("K", True), (None, True),
                            ("F", False), ("°C", False), ("", False), (22, False)):
            cases.append(({"measurement_unit": unit}, valid))
        for payload, valid in cases:
            with self.subTest(payload=payload):
                self.assert_contract("devices", "update_device", payload, valid)

    def test_user_device_registration(self):
        operation = self.documents["devices"]["paths"]["/devices"]["post"]
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        validator = self.validator("devices", schema)
        request = {
            "model_id": MODEL_ID,
            "name": "Свет в гостиной",
            "location": "Living Room",
            "endpoint": "https://partner.example/devices/light-1",
        }
        validator.validate(request)
        validator.validate({**request, "attributes": {}})
        validator.validate({**request, "attributes": {
            "video_endpoint": "https://partner.example/cameras/1/video",
        }})
        # The chosen model is resolved at runtime; all supported unit values are
        # valid request shapes, and omission lets the service choose its default.
        for unit in ("Cel", "[degF]", "K", None):
            with self.subTest(registration_unit=unit):
                validator.validate({**request, "measurement_unit": unit})

        # Registration must not allow callers to select another owner or server fields.
        protected_fields = {
            "owner_id": DEVICE_ID,
            "id": DEVICE_ID,
            "created_at": "2026-10-05T09:00:00Z",
            "updated_at": "2026-10-05T09:00:00Z",
            "device_type": "LIGHT_RELAY",
            "model": {},
        }
        for field, value in protected_fields.items():
            with self.subTest(protected_field=field):
                self.assertFalse(validator.is_valid({**request, field: value}))
        for field in request:
            with self.subTest(missing_field=field):
                self.assertFalse(validator.is_valid({k: v for k, v in request.items() if k != field}))
        for changes in (
            {"model_id": "not-a-uuid"}, {"name": ""}, {"location": ""},
            {"endpoint": None}, {"endpoint": 123}, {"attributes": None}, {"attributes": []},
            {"measurement_unit": "F"}, {"measurement_unit": "°C"},
            {"measurement_unit": ""}, {"measurement_unit": 22},
        ):
            with self.subTest(invalid_registration=changes):
                self.assertFalse(validator.is_valid({**request, **changes}))

        created = operation["responses"]["201"]
        resource = created["headers"]["Location"]["example"]
        self.assertEqual(resource.rsplit("/", 1)[0], "/api/v1/devices")
        self.assertIn("get", self.documents["devices"]["paths"]["/devices/{device_id}"])

    def test_device_measurement_unit_matches_type(self):
        for device_type in ("TEMPERATURE_SENSOR", "THERMOSTAT", "LIGHT_RELAY",
                            "GATE_CONTROLLER", "CAMERA"):
            thermal = device_type in ("TEMPERATURE_SENSOR", "THERMOSTAT")
            attributes = ({"video_endpoint": "https://partner.example/cameras/1/video"}
                          if device_type == "CAMERA" else {})
            device = {
                "id": DEVICE_ID,
                "owner_id": DEVICE_ID,
                "model_id": MODEL_ID,
                "name": "Устройство",
                "location": "Living Room",
                "endpoint": "https://partner.example/devices/1",
                "attributes": attributes,
                "device_type": device_type,
                "created_at": "2026-10-05T09:00:00Z",
                "updated_at": "2026-10-05T09:00:00Z",
                "model": {
                    "id": MODEL_ID,
                    "name": "Модель устройства",
                    "device_type": device_type,
                    "protocols": [{"name": "HTTPS"}],
                },
            }
            for unit in ("Cel", "[degF]", "K", None, "F", "°C", "", 22):
                valid = (unit in ("Cel", "[degF]", "K")) if thermal else unit is None
                with self.subTest(device_type=device_type, unit=unit):
                    self.assert_contract("devices", "device", {**device, "measurement_unit": unit}, valid)
            with self.subTest(device_type=device_type, missing_unit=True):
                self.assert_contract("devices", "device", device, False)
            if device_type == "CAMERA":
                for invalid_attributes in ({}, {"video_endpoint": None}, [], None):
                    with self.subTest(camera_attributes=invalid_attributes):
                        self.assert_contract("devices", "device", {
                            **device, "measurement_unit": None, "attributes": invalid_attributes,
                        }, False)

    def test_scenario_and_commands_without_room(self):
        # A scenario addresses devices by stable IDs; its request has no shared room.
        request = {
            "name": "Уход из дома",
            "commands": [
                {"id": COMMAND_ID, "device_id": DEVICE_ID, "parameter": "power", "value": "off"},
                {"id": "00000007-0000-4000-8000-000000000002",
                 "device_id": "00000003-0000-4000-8000-000000000002",
                 "parameter": "lock_state", "value": "locked"},
            ],
        }
        paths = self.documents["scenarios"]["paths"]
        for path, method in (("/scenarios", "post"), ("/scenarios/{scenario_id}", "put")):
            operation = paths[path][method]
            schema = operation["requestBody"]["content"]["application/json"]["schema"]
            self.validator("scenarios", schema).validate(request)
        self.assert_contract("scenarios", "scenario", {
            **request, "id": "00000006-0000-4000-8000-000000000001", "owner_id": DEVICE_ID,
        }, True)

        # These calls must be complete without a location query parameter.
        calls = [
            (paths["/scenarios"]["post"], {}),
            (paths["/scenarios/{scenario_id}"]["put"], {}),
            (paths["/scenarios/{scenario_id}"]["post"], {}),
        ]
        parameter_paths = self.documents["parameters"]["paths"]
        for template in request["commands"]:
            command = {k: v for k, v in template.items() if k != "id"}
            self.assert_contract("parameters", "command", command, True)
            calls.append((parameter_paths["/execution"]["post"], {}))
            calls.append((parameter_paths["/validation"]["get"], command))
        for operation, query in calls:
            with self.subTest(operation=operation["operationId"], query=query):
                required = {p["name"] for p in operation.get("parameters", [])
                            if p["in"] == "query" and p.get("required", False)}
                self.assertFalse(required - query.keys(), "Request is missing required query parameters")

    def test_scenario_resource_address(self):
        paths = self.documents["scenarios"]["paths"]
        created = paths["/scenarios"]["post"]["responses"]["201"]
        location = created["headers"]["Location"]["example"]
        resource = paths["/scenarios/{scenario_id}"]
        scenario_id = location.rsplit("/", 1)[1]
        self.assertEqual(location, f"/api/v1/scenarios/{scenario_id}")
        for method in ("get", "put", "delete", "post"):
            operation = resource[method]
            identifiers = [p for p in operation["parameters"] if p["name"] == "scenario_id"]
            self.assertEqual(len(identifiers), 1)
            self.assertEqual(identifiers[0]["in"], "path")
            self.assertTrue(identifiers[0]["required"])
        self.assertNotIn("put", paths["/scenarios"])
        self.assertNotIn("delete", paths["/scenarios"])

    def test_model_resource_address(self):
        document = self.documents["devices"]
        paths = document["paths"]
        created = paths["/models"]["post"]["responses"]["201"]
        location = created["headers"]["Location"]
        self.assertEqual(location["example"].rsplit("/", 1)[0], "/api/v1/models")
        self.assertIn("delete", paths["/models/{model_id}"])

    def test_device_deletion_contract(self):
        document = self.documents["devices"]
        resource = document["paths"]["/devices/{device_id}"]
        operation = resource["delete"]
        self.assertEqual(operation["operationId"], "delete_device")
        self.assertNotIn("requestBody", operation)
        parameters = operation["parameters"]
        self.assertEqual(
            {(p["in"], p["name"]) for p in parameters},
            {("path", "device_id")},
        )
        identifier = next(p for p in parameters if p["name"] == "device_id")
        self.assertTrue(identifier["required"])
        id_validator = self.validator("devices", identifier["schema"])
        id_validator.validate(DEVICE_ID)
        for invalid_id in ("not-a-uuid", "", 1, None):
            with self.subTest(invalid_device_id=invalid_id):
                self.assertFalse(id_validator.is_valid(invalid_id))

        security = operation.get("security", document["security"])
        self.assertTrue(any("bearer_auth" in requirement for requirement in security))
        responses = operation["responses"]
        self.assertNotIn("content", responses["204"])
        self.assertNotIn("404", responses)
        for status in ("400", "401", "403", "503"):
            with self.subTest(error_status=status):
                self.assertEqual(responses[status]["$ref"], f"#/components/responses/error_{status}")
                response = document["components"]["responses"][f"error_{status}"]
                schema = response["content"]["application/json"]["schema"]
                self.validator("devices", schema).validate(ERROR)

    def reading(self, name, value):
        """Build response fixtures; no polling, persistence or conversion is simulated."""
        reading = {
            "device_id": DEVICE_ID,
            "name": name,
            "value": value,
            "status": "active" if value is not None else None,
            "last_updated": TIMESTAMP if value is not None else None,
            "stale": value is None,
        }
        if name in ("temperature", "target_temperature") and value is not None:
            reading["unit"] = "Cel"
        return reading

    def test_readings(self):
        cases = [
            ("temperature", -20.5, True),
            ("temperature", 0, True),
            ("target_temperature", 22, True),
            ("power", "off", True),
            ("lock_state", "unlocked", True),
            ("available", False, True),
            ("temperature", "on", False),
            ("target_temperature", True, False),
            ("power", "unlocked", False),
            ("lock_state", 1, False),
            ("available", "false", False),
            ("available", 0, False),
            ("unknown", None, False),
        ]
        for name in ("temperature", "target_temperature", "power", "lock_state", "available"):
            cases.append((name, None, True))
        for name, value, valid in cases:
            with self.subTest(name=name, value=value):
                self.assert_contract("monitoring", "reading", self.reading(name, value), valid)
        for name in ("temperature", "target_temperature"):
            reading = self.reading(name, 22)
            with self.subTest(name=name, missing_unit=True):
                self.assert_contract("monitoring", "reading", {
                    k: v for k, v in reading.items() if k != "unit"
                }, False)
            for unit, value in (("Cel", 22), ("[degF]", 71.6), ("K", 295.15)):
                with self.subTest(name=name, unit=unit):
                    measured = {**reading, "unit": unit, "value": value,
                                "source_time": TIMESTAMP}
                    self.assert_contract("monitoring", "reading", measured, True)
                    missing = {**self.reading(name, None), "unit": unit}
                    self.assert_contract("monitoring", "reading", missing, True)
                    self.assert_contract("monitoring", "reading", {**missing, "source_time": TIMESTAMP}, False)
            for unit in ("F", "°C", "", None, 22):
                for value in (22, None):
                    with self.subTest(name=name, invalid_unit=unit, value=value):
                        self.assert_contract("monitoring", "reading", {
                            **self.reading(name, value), "unit": unit,
                        }, False)
        for name, value in (("power", "off"), ("lock_state", "unlocked"), ("available", False)):
            for reading_value in (value, None):
                reading = self.reading(name, reading_value)
                for unit in ("Cel", None):
                    with self.subTest(name=name, value=reading_value, forbidden_unit=unit):
                        self.assert_contract("monitoring", "reading", {**reading, "unit": unit}, False)
                with self.subTest(name=name, value=reading_value, source_time=True):
                    self.assert_contract("monitoring", "reading", {
                        **reading, "source_time": TIMESTAMP,
                    }, reading_value is not None)

    def test_reading_fresh_saved_and_missing_shapes(self):
        for name, value in (("temperature", 0), ("target_temperature", 22),
                            ("power", "off"), ("lock_state", "locked"), ("available", False)):
            reading = self.reading(name, value)
            for stale in (False, True):
                with self.subTest(name=name, stale=stale):
                    # Both fresh and fallback responses carry the complete snapshot.
                    self.assert_contract("monitoring", "reading", {**reading, "stale": stale}, True)
                    self.assert_contract("monitoring", "reading", {
                        **reading, "stale": stale, "source_time": TIMESTAMP,
                    }, True)
            for field in ("device_id", "name", "value", "status", "last_updated", "stale"):
                with self.subTest(name=name, missing_field=field):
                    self.assert_contract("monitoring", "reading", {
                        k: v for k, v in reading.items() if k != field
                    }, False)
            for changes in (
                {"status": None}, {"status": ""}, {"status": False},
                {"last_updated": None}, {"last_updated": "not-a-date"},
                {"source_time": None}, {"source_time": "not-a-date"},
                {"stale": None}, {"stale": "true"}, {"stale": 0},
            ):
                with self.subTest(name=name, invalid_snapshot=changes):
                    self.assert_contract("monitoring", "reading", {**reading, **changes}, False)
            self.assert_contract("monitoring", "reading", {**reading, "status": "inactive"}, True)
            missing = self.reading(name, None)
            self.assert_contract("monitoring", "reading", missing, True)
            for changes in ({"status": "active"}, {"last_updated": TIMESTAMP},
                            {"stale": False}, {"source_time": TIMESTAMP}, {"source_time": None}):
                with self.subTest(name=name, invalid_missing_snapshot=changes):
                    self.assert_contract("monitoring", "reading", {**missing, **changes}, False)
            for field in ("status", "last_updated", "stale"):
                with self.subTest(name=name, absent_missing_snapshot_field=field):
                    self.assert_contract("monitoring", "reading", {
                        k: v for k, v in missing.items() if k != field
                    }, False)

    def test_explicit_observation_recording(self):
        operation = self.documents["monitoring"]["paths"]["/observations/{device_id}"]["patch"]
        self.assertTrue(operation["requestBody"]["required"])
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        validator = self.validator("monitoring", schema)
        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        response_validator = self.validator("monitoring", response_schema)
        recorded = self.reading("temperature", 0)
        response_validator.validate(recorded)
        self.assertFalse(response_validator.is_valid({**recorded, "stale": True}))
        self.assertFalse(response_validator.is_valid(self.reading("temperature", None)))
        device_parameter = next(p for p in operation["parameters"] if p["name"] == "device_id")
        self.assertEqual(device_parameter["in"], "path")
        self.assertTrue(device_parameter["required"])
        id_validator = self.validator("monitoring", device_parameter["schema"])
        id_validator.validate(DEVICE_ID)
        self.assertFalse(id_validator.is_valid("not-a-uuid"))
        for name, value in (("temperature", 0), ("target_temperature", -5.5),
                            ("power", "off"), ("lock_state", "unlocked"), ("available", False)):
            request = {"name": name, "value": value, "status": "active"}
            thermal = name in ("temperature", "target_temperature")
            if thermal:
                request["unit"] = "Cel"
            with self.subTest(name=name, valid_recording=True):
                validator.validate(request)
                validator.validate({**request, "status": "inactive", "source_time": TIMESTAMP})
            if thermal:
                for unit in ("Cel", "[degF]", "K"):
                    with self.subTest(name=name, unit=unit):
                        validator.validate({**request, "unit": unit})
                self.assertFalse(validator.is_valid({k: v for k, v in request.items() if k != "unit"}))
            else:
                self.assertFalse(validator.is_valid({**request, "unit": "Cel"}))
            for field in ("name", "value", "status"):
                with self.subTest(name=name, missing_field=field):
                    self.assertFalse(validator.is_valid({k: v for k, v in request.items() if k != field}))
            for changes in (
                {"value": None}, {"value": {}}, {"value": []}, {"name": "unknown"},
                {"status": None}, {"status": ""}, {"status": True},
                {"source_time": None}, {"source_time": "not-a-date"},
                {"unit": None}, {"unit": "F"},
            ):
                with self.subTest(name=name, invalid_recording=changes):
                    self.assertFalse(validator.is_valid({**request, **changes}))
            server_fields = {
                "device_id": DEVICE_ID, "owner_id": DEVICE_ID,
                "last_updated": TIMESTAMP, "stale": False,
                "created_at": TIMESTAMP,
            }
            for field, field_value in server_fields.items():
                with self.subTest(name=name, protected_recording_field=field):
                    self.assertFalse(validator.is_valid({**request, field: field_value}))
        for name, value in (("temperature", "22"), ("target_temperature", False),
                            ("power", "unlocked"), ("lock_state", "on"), ("available", 0)):
            request = {"name": name, "value": value, "status": "active"}
            if name in ("temperature", "target_temperature"):
                request["unit"] = "Cel"
            with self.subTest(name=name, incompatible_value=value):
                self.assertFalse(validator.is_valid(request))

    def execution(self, status, outcomes):
        commands = []
        for index, outcome in enumerate(outcomes):
            command = {
                "scenario_command_id": f"00000007-0000-4000-8000-{index + 1:012d}",
                "device_id": DEVICE_ID,
                "command_index": index,
                "status": outcome,
            }
            if outcome == "FAILED":
                command["error"] = deepcopy(ERROR)
            commands.append(command)
        return {"scenario_id": DEVICE_ID, "status": status, "commands": commands}

    def test_execution_summary(self):
        cases = [
            ("SUCCEEDED", ["SUCCEEDED"], True),
            ("PARTIAL", ["SUCCEEDED", "FAILED", "NOT_SENT"], True),
            ("FAILED", ["FAILED", "NOT_SENT"], True),
            ("SUCCEEDED", [], False),
            ("SUCCEEDED", ["FAILED"], False),
            ("SUCCEEDED", ["NOT_SENT"], False),
            ("PARTIAL", ["SUCCEEDED"], False),
            ("PARTIAL", ["FAILED"], False),
            ("PARTIAL", ["SUCCEEDED", "NOT_SENT"], False),
            ("FAILED", ["SUCCEEDED", "FAILED"], False),
            ("FAILED", ["NOT_SENT"], False),
            ("FAILED", ["FAILED", "FAILED"], False),
            ("SUCCEEDED", ["SUCCEEDED"] * 20, True),
            ("SUCCEEDED", ["SUCCEEDED"] * 21, False),
        ]
        for status, outcomes, valid in cases:
            with self.subTest(status=status, outcomes=outcomes):
                self.assert_contract("scenarios", "execution_response", self.execution(status, outcomes), valid)

    def test_command_error_and_index(self):
        command = self.execution("FAILED", ["FAILED"])["commands"][0]
        for index, valid in ((-1, False), (0, True), (19, True), (20, False)):
            self.assert_contract("scenarios", "command_outcome", {**command, "command_index": index}, valid)
        del command["error"]
        self.assert_contract("scenarios", "command_outcome", command, False)
        command["status"] = "SUCCEEDED"
        command["error"] = ERROR
        self.assert_contract("scenarios", "command_outcome", command, False)
        response = self.execution("SUCCEEDED", ["SUCCEEDED"])
        response["error"] = ERROR
        self.assert_contract("scenarios", "execution_response", response, False)
