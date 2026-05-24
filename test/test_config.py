import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
ROOT_CONFIG_FILE = ROOT_DIR / "config.json"


def _has_sqlalchemy() -> bool:
    try:
        import sqlalchemy
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
    except Exception:
        return False
    return getattr(sqlalchemy, "__spec__", None) is not None and create_engine is not None and sessionmaker is not None


class ConfigLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._created_root_config = False
        if not ROOT_CONFIG_FILE.exists():
            ROOT_CONFIG_FILE.write_text(json.dumps({"auth-key": "test-auth"}), encoding="utf-8")
            cls._created_root_config = True

        old_env_auth_key = os.environ.get("WEBCHAT2API_AUTH_KEY")
        os.environ["WEBCHAT2API_AUTH_KEY"] = "test-auth"
        try:
            from services import config as config_module

            cls.config_module = config_module
        finally:
            if old_env_auth_key is None:
                os.environ.pop("WEBCHAT2API_AUTH_KEY", None)
            else:
                os.environ["WEBCHAT2API_AUTH_KEY"] = old_env_auth_key

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._created_root_config and ROOT_CONFIG_FILE.exists():
            ROOT_CONFIG_FILE.unlink()

    def test_load_settings_ignores_directory_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            data_dir = base_dir / "data"
            config_dir = base_dir / "config.json"
            os_auth_key = "env-auth"

            config_dir.mkdir()

            module = self.config_module
            old_base_dir = module.BASE_DIR
            old_data_dir = module.DATA_DIR
            old_config_file = module.CONFIG_FILE
            old_env_auth_key = module.os.environ.get("WEBCHAT2API_AUTH_KEY")
            old_login_secret = module.os.environ.get("LOGIN_SECRET")
            try:
                module.BASE_DIR = base_dir
                module.DATA_DIR = data_dir
                module.CONFIG_FILE = config_dir
                module.os.environ.pop("WEBCHAT2API_AUTH_KEY", None)
                module.os.environ["LOGIN_SECRET"] = os_auth_key

                settings = module._load_settings()

                self.assertEqual(settings.auth_key, os_auth_key)
                self.assertEqual(settings.refresh_account_interval_minute, 5)
            finally:
                module.BASE_DIR = old_base_dir
                module.DATA_DIR = old_data_dir
                module.CONFIG_FILE = old_config_file
                if old_env_auth_key is None:
                    module.os.environ.pop("WEBCHAT2API_AUTH_KEY", None)
                else:
                    module.os.environ["WEBCHAT2API_AUTH_KEY"] = old_env_auth_key
                if old_login_secret is None:
                    module.os.environ.pop("LOGIN_SECRET", None)
                else:
                    module.os.environ["LOGIN_SECRET"] = old_login_secret

    def _isolated_store(self, base_dir: Path, *, config_data: dict[str, object] | None = None, env: dict[str, str | None] | None = None):
        module = self.config_module
        data_dir = base_dir / "data"
        config_file = base_dir / "config.json"
        data_dir.mkdir(parents=True, exist_ok=True)
        if config_data is not None:
            config_file.write_text(json.dumps(config_data), encoding="utf-8")

        old_base_dir = module.BASE_DIR
        old_data_dir = module.DATA_DIR
        old_config_file = module.CONFIG_FILE
        env = env or {}
        saved_env = {key: module.os.environ.get(key) for key in env}

        def restore() -> None:
            module.BASE_DIR = old_base_dir
            module.DATA_DIR = old_data_dir
            module.CONFIG_FILE = old_config_file
            for key, value in saved_env.items():
                if value is None:
                    module.os.environ.pop(key, None)
                else:
                    module.os.environ[key] = value

        self.addCleanup(restore)
        module.BASE_DIR = base_dir
        module.DATA_DIR = data_dir
        module.CONFIG_FILE = config_file
        for key, value in env.items():
            if value is None:
                module.os.environ.pop(key, None)
            else:
                module.os.environ[key] = value
        return module.ConfigStore(config_file)

    def test_proxy_url_overrides_get_proxy_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self._isolated_store(
                Path(tmp_dir),
                config_data={"auth-key": "file-auth", "proxy": "http://stored-proxy"},
                env={"STORAGE_BACKEND": "json", "PROXY_URL": "http://env-proxy"},
            )

            self.assertEqual(store.get()["proxy"], "http://env-proxy")
            self.assertEqual(store.get_proxy_settings(), "http://env-proxy")
            self.assertNotIn("auth-key", store.get())

    def test_sqlite_storage_settings_reload_without_config_json(self) -> None:
        if not _has_sqlalchemy():
            self.skipTest("SQLAlchemy is not installed on this host")

        from services.storage.database_storage import DatabaseStorageBackend

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            database_url = f"sqlite:///{base_dir / 'settings.db'}"
            env = {
                "STORAGE_BACKEND": "sqlite",
                "DATABASE_URL": database_url,
                "LOGIN_SECRET": "env-auth",
                "PROXY_URL": None,
            }
            DatabaseStorageBackend(database_url)
            first = self._isolated_store(base_dir, env=env)

            first.update({"proxy": "http://saved-proxy", "global_system_prompt": "persist me"})
            self.assertFalse((base_dir / "config.json").exists())
            second = self._isolated_store(base_dir, env=env)

            self.assertEqual(second.get()["proxy"], "http://saved-proxy")
            self.assertEqual(second.get()["global_system_prompt"], "persist me")
            self.assertNotIn("auth-key", second.get())

    def test_json_storage_settings_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            env = {"STORAGE_BACKEND": "json", "LOGIN_SECRET": "env-auth", "PROXY_URL": None}
            first = self._isolated_store(base_dir, env=env)

            first.update({"proxy": "http://json-proxy", "image_retention_days": 9})
            self.assertFalse((base_dir / "config.json").exists())
            second = self._isolated_store(base_dir, env=env)

            self.assertEqual(second.get()["proxy"], "http://json-proxy")
            self.assertEqual(second.get()["image_retention_days"], 9)
            self.assertTrue((base_dir / "data" / "settings.json").exists())
            self.assertFalse((base_dir / "config.json").exists())

    def test_update_ignores_unknown_and_secret_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self._isolated_store(
                Path(tmp_dir),
                env={"STORAGE_BACKEND": "json", "LOGIN_SECRET": "env-auth", "PROXY_URL": None},
            )

            result = store.update({
                "proxy": "http://allowed-proxy",
                "auth-key": "posted-secret",
                "backup_state": {"last_run": "now"},
                "unexpected_secret": "do-not-store",
            })

            self.assertEqual(result["proxy"], "http://allowed-proxy")
            self.assertNotIn("auth-key", result)
            self.assertNotIn("backup_state", store.data)
            self.assertNotIn("unexpected_secret", store.data)
            settings = json.loads((Path(tmp_dir) / "data" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings, {"proxy": "http://allowed-proxy"})

    def test_config_json_initial_values_migrate_to_storage(self) -> None:
        if not _has_sqlalchemy():
            self.skipTest("SQLAlchemy is not installed on this host")

        from services.storage.database_storage import DatabaseStorageBackend

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            database_url = f"sqlite:///{base_dir / 'settings.db'}"
            env = {
                "STORAGE_BACKEND": "database",
                "DATABASE_URL": database_url,
                "LOGIN_SECRET": "env-auth",
                "PROXY_URL": None,
            }
            DatabaseStorageBackend(database_url)
            first = self._isolated_store(
                base_dir,
                config_data={"proxy": "http://migrated-proxy", "refresh_account_interval_minute": 11},
                env=env,
            )

            self.assertEqual(first.get()["proxy"], "http://migrated-proxy")
            (base_dir / "config.json").unlink()
            second = self._isolated_store(base_dir, env=env)

            self.assertEqual(second.get()["proxy"], "http://migrated-proxy")
            self.assertEqual(second.refresh_account_interval_minute, 11)
            self.assertNotIn("auth-key", second.get())

    def test_network_profile_settings_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self._isolated_store(
                Path(tmp_dir),
                env={"STORAGE_BACKEND": "json", "LOGIN_SECRET": "env-auth", "PROXY_URL": None},
            )

            result = store.update({
                "chatgpt_fingerprint": {"impersonate": "edge101", "user-agent": "GPT UA", "cookie": "drop-me"},
                "grok_console_fingerprint": {"impersonate": "chrome136", "user-agent": "Legacy UA", "cookie": "drop-me"},
                "network_profiles": {
                    "grok_console": {
                        "impersonate": "chrome137",
                        "user-agent": "Profile UA",
                        "verify": True,
                        "timeout": 60,
                        "cf_clearance": "profile-clearance",
                        "cookie": "drop-me",
                    },
                    "grok_app_chat": {
                        "browser": "chrome136",
                        "user-agent": "App UA",
                        "cf_cookies": "cf_bm=profile-bm",
                        "sec-ch-ua": "app sec ua",
                        "statsig_id": "statsig-profile",
                    },
                    "unknown_provider": {"user-agent": "Ignore Me"},
                },
                "unknown_fingerprint": {"user-agent": "Ignore Me"},
            })

            self.assertEqual(result["chatgpt_fingerprint"]["user-agent"], "GPT UA")
            self.assertEqual(result["grok_console_fingerprint"]["user-agent"], "Legacy UA")
            self.assertEqual(result["network_profiles"]["grok_console"]["impersonate"], "chrome137")
            self.assertEqual(result["network_profiles"]["grok_console"]["cf_clearance"], "profile-clearance")
            self.assertEqual(result["network_profiles"]["grok_app_chat"]["browser"], "chrome136")
            self.assertEqual(result["network_profiles"]["grok_app_chat"]["cf_cookies"], "cf_bm=profile-bm")
            self.assertEqual(result["network_profiles"]["grok_app_chat"]["sec-ch-ua"], "app sec ua")
            self.assertEqual(result["network_profiles"]["grok_app_chat"]["statsig_id"], "statsig-profile")
            self.assertNotIn("cookie", result["chatgpt_fingerprint"])
            self.assertNotIn("cookie", result["grok_console_fingerprint"])
            self.assertNotIn("cookie", result["network_profiles"]["grok_console"])
            self.assertNotIn("unknown_provider", result["network_profiles"])
            settings = json.loads((Path(tmp_dir) / "data" / "settings.json").read_text(encoding="utf-8"))
            self.assertIn("chatgpt_fingerprint", settings)
            self.assertIn("grok_console_fingerprint", settings)
            self.assertIn("network_profiles", settings)
            self.assertEqual(settings["network_profiles"]["grok_console"]["cf_clearance"], "profile-clearance")
            self.assertEqual(settings["network_profiles"]["grok_app_chat"]["cf_cookies"], "cf_bm=profile-bm")
            self.assertNotIn("unknown_fingerprint", settings)

    def test_flaresolverr_settings_persist_with_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self._isolated_store(
                Path(tmp_dir),
                env={"STORAGE_BACKEND": "json", "LOGIN_SECRET": "env-auth", "PROXY_URL": None},
            )

            self.assertEqual(store.flaresolverr_url, "")
            self.assertEqual(store.flaresolverr_timeout_sec, 60)
            result = store.update({"flaresolverr_url": " http://solver.local/ ", "flaresolverr_timeout_sec": "90"})

            self.assertEqual(store.flaresolverr_url, "http://solver.local")
            self.assertEqual(store.flaresolverr_timeout_sec, 90)
            self.assertEqual(result["flaresolverr_url"], "http://solver.local")
            self.assertEqual(result["flaresolverr_timeout_sec"], 90)
            settings = json.loads((Path(tmp_dir) / "data" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["flaresolverr_url"], " http://solver.local/ ")
            self.assertEqual(settings["flaresolverr_timeout_sec"], "90")


if __name__ == "__main__":
    unittest.main()
