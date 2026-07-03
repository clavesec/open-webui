import atexit
import logging
import os
import time
import uuid

import docker
import pytest
from docker import DockerClient
from pytest_docker.plugin import get_docker_ip
from fastapi.testclient import TestClient
from sqlalchemy import text, create_engine


log = logging.getLogger(__name__)


def get_fast_api_client():
    from main import app

    with TestClient(app) as c:
        return c


class AbstractIntegrationTest:
    BASE_PATH = None

    def create_url(self, path="", query_params=None):
        if self.BASE_PATH is None:
            raise Exception("BASE_PATH is not set")
        parts = self.BASE_PATH.split("/")
        parts = [part.strip() for part in parts if part.strip() != ""]
        path_parts = path.split("/")
        path_parts = [part.strip() for part in path_parts if part.strip() != ""]
        query_parts = ""
        if query_params:
            query_parts = "&".join(
                [f"{key}={value}" for key, value in query_params.items()]
            )
            query_parts = f"?{query_parts}"
        return "/".join(parts + path_parts) + query_parts

    @classmethod
    def setup_class(cls):
        pass

    def setup_method(self):
        pass

    @classmethod
    def teardown_class(cls):
        pass

    def teardown_method(self):
        pass


class AbstractPostgresTest(AbstractIntegrationTest):
    DOCKER_CONTAINER_NAME: str  # Set dynamically in setup_class
    docker_client: DockerClient
    container = None  # Docker container object

    @classmethod
    def _create_db_url(cls, env_vars_postgres: dict, port: int) -> str:
        host = get_docker_ip()
        user = env_vars_postgres["POSTGRES_USER"]
        pw = env_vars_postgres["POSTGRES_PASSWORD"]
        db = env_vars_postgres["POSTGRES_DB"]
        return f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.DOCKER_CONTAINER_NAME = (
            f"postgres-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        try:
            env_vars_postgres = {
                "POSTGRES_USER": "user",
                "POSTGRES_PASSWORD": "example",
                "POSTGRES_DB": "openwebui",
            }
            cls.docker_client = docker.from_env()
            cls.container = cls.docker_client.containers.run(
                "postgres:16.2",
                detach=True,
                environment=env_vars_postgres,
                name=cls.DOCKER_CONTAINER_NAME,
                ports={5432: None},
                command="postgres -c log_statement=all",
            )

            cls.container.reload()
            assigned_port = int(cls.container.ports["5432/tcp"][0]["HostPort"])

            def _cleanup():
                try:
                    cls.docker_client.containers.get(cls.DOCKER_CONTAINER_NAME).remove(
                        force=True
                    )
                except Exception:
                    pass  # Already removed by teardown_class

            atexit.register(_cleanup)

            database_url = cls._create_db_url(env_vars_postgres, assigned_port)
            os.environ["DATABASE_URL"] = database_url
            retries = 10
            db = None
            while retries > 0:
                try:
                    from open_webui.config import OPEN_WEBUI_DIR

                    db = create_engine(database_url, pool_pre_ping=True)
                    db = db.connect()
                    log.info("postgres is ready!")
                    break
                except Exception as e:
                    log.warning(e)
                    time.sleep(3)
                    retries -= 1

            if db:
                # import must be after setting env!
                cls.fast_api_client = get_fast_api_client()
                db.close()
            else:
                raise Exception("Could not connect to Postgres")
        except Exception as ex:
            log.error(ex)
            cls.teardown_class()
            pytest.fail(f"Could not setup test environment: {ex}")

    def _check_db_connection(self):
        from open_webui.internal.db import Session

        retries = 10
        while retries > 0:
            try:
                Session.execute(text("SELECT 1"))
                Session.commit()
                break
            except Exception as e:
                Session.rollback()
                log.warning(e)
                time.sleep(3)
                retries -= 1

    def setup_method(self):
        super().setup_method()
        self._check_db_connection()

    @classmethod
    def teardown_class(cls) -> None:
        super().teardown_class()
        if cls.container:
            cls.container.remove(force=True)

    def teardown_method(self):
        from open_webui.internal.db import Session

        # rollback everything not yet committed
        Session.commit()

        # Dynamic table truncation — handles new tables automatically
        result = Session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        )
        tables = [row[0] for row in result.fetchall()]
        for table in tables:
            Session.execute(text(f'TRUNCATE TABLE "{table}" CASCADE'))
        Session.commit()
