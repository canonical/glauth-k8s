# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from unittest.mock import MagicMock

from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

CONTAINER_NAME = "glauth"
DB_USERNAME = "fake_relation_id_1"
DB_PASSWORD = "fake-password"
DB_ENDPOINTS = "postgresql-k8s-primary.namespace.svc.cluster.local:5432"


def setup_postgres_relation(harness: Harness) -> None:
    db_relation_id = harness.add_relation("database", "postgresql-k8s")
    harness.add_relation_unit(db_relation_id, "postgresql-k8s/0")
    harness.update_relation_data(
        db_relation_id,
        "postgresql-k8s",
        {
            "data": '{"database": "database", "extra-user-roles": "SUPERUSER"}',
            "endpoints": DB_ENDPOINTS,
            "password": DB_PASSWORD,
            "username": DB_USERNAME,
        },
    )


def setup_ingress_relation(harness: Harness) -> int:
    relation_id = harness.add_relation("ingress", "traefik")
    harness.add_relation_unit(relation_id, "traefik/0")
    url = f"http://ingress:80/{harness.model.name}-glauth"
    harness.update_relation_data(
        relation_id,
        "traefik",
        {"ingress": json.dumps({"url": url})},
    )
    return relation_id


def setup_loki_relation(harness: Harness) -> int:
    relation_id = harness.add_relation("logging", "loki-k8s")
    harness.add_relation_unit(relation_id, "loki-k8s/0")
    databag = {
        "promtail_binary_zip_url": json.dumps(
            {
                "amd64": {
                    "filename": "promtail-static-amd64",
                    "zipsha": "543e333b0184e14015a42c3c9e9e66d2464aaa66eca48b29e185a6a18f67ab6d",
                    "binsha": "17e2e271e65f793a9fbe81eab887b941e9d680abe82d5a0602888c50f5e0cac9",
                    "url": "https://github.com/canonical/loki-k8s-operator/releases/download/promtail-v2.5.0/promtail-static-amd64.gz",
                }
            }
        ),
    }
    unit_databag = {
        "endpoint": json.dumps(
            {
                "url": "http://loki-k8s-0.loki-k8s-endpoints.model0.svc.cluster.local:3100/loki/api/v1/push"
            }
        )
    }
    harness.update_relation_data(
        relation_id,
        "loki-k8s/0",
        unit_databag,
    )
    harness.update_relation_data(
        relation_id,
        "loki-k8s",
        databag,
    )
    return relation_id


def trigger_database_changed(harness: Harness) -> None:
    db_relation_id = harness.add_relation("database", "postgresql-k8s")
    harness.add_relation_unit(db_relation_id, "postgresql-k8s/0")
    harness.update_relation_data(
        db_relation_id,
        "postgresql-k8s",
        {
            "data": '{"database": "database", "extra-user-roles": "SUPERUSER"}',
            "endpoints": DB_ENDPOINTS,
        },
    )


def test_on_pebble_ready_cannot_connect_container(harness: Harness) -> None:
    harness.set_can_connect(CONTAINER_NAME, False)

    container = harness.model.unit.get_container(CONTAINER_NAME)
    harness.charm.on.glauth_pebble_ready.emit(container)

    assert isinstance(harness.model.unit.status, WaitingStatus)


def test_on_pebble_ready_correct_plan(harness: Harness) -> None:
    container = harness.model.unit.get_container(CONTAINER_NAME)
    harness.charm.on.glauth_pebble_ready.emit(container)

    expected_plan = {
        "services": {
            CONTAINER_NAME: {
                "override": "replace",
                "summary": "GLAuth Operator layer",
                "startup": "disabled",
                "command": '/bin/sh -c "glauth -c /etc/config/glauth.cfg 2>&1 | tee /var/log/glauth.log"',
            }
        }
    }
    updated_plan = harness.get_container_pebble_plan(CONTAINER_NAME).to_dict()
    assert expected_plan == updated_plan


def test_on_pebble_ready_service_not_started_when_database_not_created(harness: Harness) -> None:
    container = harness.model.unit.get_container(CONTAINER_NAME)
    harness.charm.on.glauth_pebble_ready.emit(container)

    service = harness.model.unit.get_container("glauth").get_service("glauth")
    assert not service.is_running()


def test_on_pebble_ready_service_started_when_database_is_created(harness: Harness) -> None:
    setup_postgres_relation(harness)

    container = harness.model.unit.get_container(CONTAINER_NAME)
    harness.charm.on.glauth_pebble_ready.emit(container)

    service = harness.model.unit.get_container("glauth").get_service("glauth")
    assert service.is_running()
    assert harness.model.unit.status == ActiveStatus()


def test_on_pebble_ready_has_correct_config_when_database_is_created(harness: Harness) -> None:
    setup_postgres_relation(harness)

    container = harness.model.unit.get_container(CONTAINER_NAME)
    harness.charm.on.glauth_pebble_ready.emit(container)

    expected_config = ""

    with open("tests/unit/test_configs/test_glauth.cfg", "r") as stream:
        expected_config = "".join(stream.readlines())
    config = harness.charm._render_conf_file()

    assert config == expected_config
    assert harness.model.unit.status == ActiveStatus()


def test_on_pebble_ready_when_missing_database_relation(harness: Harness) -> None:
    container = harness.model.unit.get_container(CONTAINER_NAME)
    harness.charm.on.glauth_pebble_ready.emit(container)

    assert isinstance(harness.model.unit.status, BlockedStatus)
    assert "Missing required relation with postgresql" in harness.charm.unit.status.message


def test_on_pebble_ready_when_database_not_created_yet(harness: Harness) -> None:
    trigger_database_changed(harness)

    container = harness.model.unit.get_container(CONTAINER_NAME)
    harness.charm.on.glauth_pebble_ready.emit(container)

    assert isinstance(harness.model.unit.status, WaitingStatus)
    assert "Waiting for database creation" in harness.charm.unit.status.message


def test_on_database_created_cannot_connect_container(harness: Harness) -> None:
    harness.set_can_connect(CONTAINER_NAME, False)

    setup_postgres_relation(harness)

    assert isinstance(harness.charm.unit.status, WaitingStatus)
    assert "Waiting to connect to glauth container" in harness.charm.unit.status.message


def test_on_pebble_ready_with_loki(harness: Harness) -> None:
    setup_postgres_relation(harness)
    setup_loki_relation(harness)
    container = harness.model.unit.get_container(CONTAINER_NAME)
    harness.charm.on.glauth_pebble_ready.emit(container)

    assert harness.model.unit.status == ActiveStatus()


def test_on_pebble_ready_make_dir_called(harness: Harness) -> None:
    container = harness.model.unit.get_container(CONTAINER_NAME)
    harness.charm.on.glauth_pebble_ready.emit(container)

    assert container.isdir("/var/log")


def test_ingress_relation(harness: Harness, mocked_fqdn: MagicMock) -> None:
    relation_id = setup_ingress_relation(harness)
    app_data = harness.get_relation_data(relation_id, harness.charm.app)

    assert app_data == {
        "host": mocked_fqdn.return_value,
        "model": harness.model.name,
        "name": "glauth-k8s",
        "port": "5555",
        "strip-prefix": "true",
    }


def test_external_url_with_ingress_ready(harness: Harness) -> None:
    setup_ingress_relation(harness)

    container = harness.model.unit.get_container(CONTAINER_NAME)
    harness.charm.on.glauth_pebble_ready.emit(container)

    external_url = harness.charm._metrics_external_url
    assert external_url == "https://ingress/glauth-model-glauth"
