#!/usr/bin/env python3
"""
DimensionDoor - Home Assistant Configuration Patcher

Ensures that HA's configuration.yaml has the required http: section
with trusted_proxies so the tunnel add-on can proxy traffic correctly.

Runs on every add-on start to ensure config is always correct.
"""

import json
import logging
import os
import shutil
import urllib.request
import urllib.error
from datetime import datetime

import yaml

logger = logging.getLogger("dimensiondoor.config")

# Path to HA configuration.yaml (mapped via config:rw in add-on config)
HA_CONFIG_PATH = "/config/configuration.yaml"

# The trusted proxy subnets required for the add-on to work
REQUIRED_PROXIES = [
    "172.30.33.0/24",   # HA add-on network
    "172.30.32.0/24",   # hassio network
    "127.0.0.1",        # loopback IPv4
    "::1",              # loopback IPv6
]


# --- Custom YAML constructors for HA's !include directives ---
# Without these, PyYAML would crash on HA's custom tags

class HAInclude:
    """Placeholder for HA !include directives to preserve them during round-trip."""
    def __init__(self, tag, value):
        self.tag = tag
        self.value = value


def _ha_include_constructor(loader, tag, node):
    """Handle all !include* YAML tags from HA."""
    value = loader.construct_scalar(node)
    return HAInclude(tag, value)


def _ha_include_representer(dumper, data):
    """Write back HA !include* tags."""
    return dumper.represent_scalar(data.tag, data.value)


# Register constructors for all known HA include variants
_HA_TAGS = [
    "!include",
    "!include_dir_list",
    "!include_dir_merge_list",
    "!include_dir_merge_named",
    "!include_dir_named",
    "!secret",
    "!env_var",
]


class HALoader(yaml.SafeLoader):
    pass


class HADumper(yaml.SafeDumper):
    pass


for tag in _HA_TAGS:
    HALoader.add_constructor(
        tag, lambda loader, node, t=tag: _ha_include_constructor(loader, t, node)
    )
    HADumper.add_representer(
        HAInclude, _ha_include_representer
    )

# Also handle any unknown tags gracefully
HALoader.add_multi_constructor(
    "!", lambda loader, tag, node: _ha_include_constructor(loader, tag, node)
)


def _backup_config(config_path: str) -> str:
    """Create a timestamped backup of configuration.yaml."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{config_path}.dimensiondoor_backup_{timestamp}"
    shutil.copy2(config_path, backup_path)
    logger.info(f"Configuration backup created: {backup_path}")
    return backup_path


def _load_config(config_path: str) -> dict:
    """Load HA configuration.yaml with custom tag support."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.load(f, Loader=HALoader)
    return config if isinstance(config, dict) else {}


def _save_config(config_path: str, config: dict):
    """Save configuration.yaml preserving HA custom tags."""
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            config,
            f,
            Dumper=HADumper,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


def _check_ha_config() -> tuple[bool, str]:
    """
    Call the HA Supervisor API to validate configuration.yaml.

    Returns (is_valid, message).
    """
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        logger.warning("SUPERVISOR_TOKEN not available - skipping config validation")
        return True, "skipped (no supervisor token)"

    url = "http://supervisor/core/api/config/core/check_config"
    headers = {
        "Authorization": f"Bearer {supervisor_token}",
        "Content-Type": "application/json",
    }

    try:
        req = urllib.request.Request(url, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            result = data.get("result", "unknown")
            errors = data.get("errors")

            if result == "valid" or (errors is None and result != "invalid"):
                return True, "Configuration is valid"
            else:
                return False, f"Configuration invalid: {errors}"
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        logger.warning(f"Config check HTTP error {e.code}: {body}")
        # Don't treat API errors as config failures
        return True, f"check unavailable (HTTP {e.code})"
    except Exception as e:
        logger.warning(f"Config check failed: {e}")
        # If we can't reach the API, don't block - assume OK
        return True, f"check unavailable ({e})"


def _restore_backup(config_path: str, backup_path: str):
    """Restore configuration.yaml from backup."""
    shutil.copy2(backup_path, config_path)
    logger.info(f"Restored configuration.yaml from backup: {backup_path}")


def ensure_http_config(config_path: str = HA_CONFIG_PATH) -> bool:
    """
    Ensure the http: section in configuration.yaml has the required
    trusted_proxies for the DimensionDoor add-on.

    After making changes, validates the config via HA's API.
    If validation fails, reverts to the backup.

    Returns True if changes were made, False if already configured.
    """
    if not os.path.exists(config_path):
        logger.warning(f"Configuration file not found: {config_path}")
        return False

    try:
        config = _load_config(config_path)
    except Exception as e:
        logger.error(f"Failed to parse configuration.yaml: {e}")
        logger.error("Please ensure your configuration.yaml is valid YAML.")
        return False

    changes_made = False

    # Ensure http: section exists
    if "http" not in config or config["http"] is None:
        config["http"] = {}
        changes_made = True
        logger.info("Added http: section to configuration.yaml")

    http_config = config["http"]

    # Ensure use_x_forwarded_for is set
    if not http_config.get("use_x_forwarded_for"):
        http_config["use_x_forwarded_for"] = True
        changes_made = True
        logger.info("Set use_x_forwarded_for: true")

    # Ensure trusted_proxies exists and contains our required entries
    if "trusted_proxies" not in http_config or http_config["trusted_proxies"] is None:
        http_config["trusted_proxies"] = []
        changes_made = True

    existing_proxies = [str(p) for p in http_config["trusted_proxies"]]

    for proxy in REQUIRED_PROXIES:
        if proxy not in existing_proxies:
            http_config["trusted_proxies"].append(proxy)
            changes_made = True
            logger.info(f"Added trusted proxy: {proxy}")

    if changes_made:
        # Backup before modifying
        backup_path = _backup_config(config_path)

        try:
            _save_config(config_path, config)
            logger.info("configuration.yaml updated. Validating...")
        except Exception as e:
            logger.error(f"Failed to write configuration.yaml: {e}")
            _restore_backup(config_path, backup_path)
            return False

        # Validate the new configuration via HA API
        is_valid, message = _check_ha_config()

        if is_valid:
            logger.info(f"Config validation: {message}")
            logger.warning(
                "NOTE: Home Assistant needs to be restarted for http: changes to take effect. "
                "If this is the first time, please restart HA from Settings > System > Restart."
            )
        else:
            logger.error(f"Config validation FAILED: {message}")
            logger.error("Reverting configuration.yaml to backup...")
            _restore_backup(config_path, backup_path)
            logger.error(
                "The DimensionDoor config could not be applied automatically. "
                "Please add the http: trusted_proxies section manually."
            )
            return False
    else:
        logger.info("configuration.yaml already has the required http: trusted_proxies config.")

    return changes_made


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    ensure_http_config()
