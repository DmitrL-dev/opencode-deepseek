"""Safely install the official DeepSeek API route into an initialized profile.

The installer merges four id-targeted rows, leaves every other row (including
the local web bridge) untouched, and never contacts DeepSeek or reads a key.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import warnings

from ruamel.yaml.error import YAMLError, YAMLWarning

from .harness import default_home, render, write_private, yaml_codec

# Importing providers.access would load the bridge's .env (possibly secrets).
# Use the same OS lock protocol without importing provider configuration.
if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _file_lock(file, unlock=False):
    if os.name == "nt":
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(file, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX)


PROFILES = ("desktop", "web", "headless")
ROUTE = "deepseek-official"
BASE_URL = "https://api.deepseek.com/anthropic"
MAX_TOKENS = 32768
TARGET_IDS = (
    "llm-deepseek",
    "agent-default-model",
    "session-title-llm",
    "session-log-deepseek",
)
MANAGED_IDS = frozenset(TARGET_IDS)
MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
PATCH_FILE = Path(__file__).with_name("harness_official.patch.yml")

_STANDARD_TAGS = frozenset({
    None,
    "",
    "tag:yaml.org,2002:map",
    "tag:yaml.org,2002:seq",
    "tag:yaml.org,2002:str",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:null",
})


@dataclass(frozen=True)
class Plan:
    """A validated, rendered profile update that has not been written."""

    path: Path
    before: bytes
    patched: bytes
    changed: bool


def _container_counts(value) -> dict:
    """Count container references so aliases can be spotted without recursion."""
    counts = {}
    visited = set()

    def visit(node):
        if isinstance(node, (dict, list)):
            counts[id(node)] = counts.get(id(node), 0) + 1
            if id(node) in visited:
                return
            visited.add(id(node))
        if isinstance(node, dict):
            for key, child in node.items():
                visit(key)
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return counts


def _assert_safe_tree(node, name, counts, visited):
    if isinstance(node, (dict, list)):
        if counts.get(id(node), 0) > 1:
            raise ValueError("Shared YAML alias in the official API entry for " + name)
        if id(node) in visited:
            raise ValueError("Recursive YAML alias in the official API entry for " + name)
        visited.add(id(node))
    anchor = getattr(node, "yaml_anchor", None)
    if callable(anchor):
        anchor_value = anchor()
        if getattr(anchor_value, "value", None) is not None:
            raise ValueError("YAML anchor in the official API entry for " + name)
    tag = getattr(node, "tag", None)
    if tag is not None and getattr(tag, "value", str(tag)) not in _STANDARD_TAGS:
        raise ValueError("Dynamic YAML value in the official API entry for " + name)
    if isinstance(node, dict):
        if getattr(node, "merge", None):
            raise ValueError("YAML merge key in the official API entry for " + name)
        for key, child in node.items():
            _assert_safe_tree(key, name, counts, visited)
            _assert_safe_tree(child, name, counts, visited)
    elif isinstance(node, list):
        for child in node:
            _assert_safe_tree(child, name, counts, visited)


def _assert_safe_target(row, name, counts):
    if set(row) - {"id", "config", "disabled"}:
        raise ValueError("Ambiguous Harness entry for " + name + "; edit the profile manually")
    if "config" in row and not isinstance(row["config"], dict):
        raise ValueError("Dynamic or unsupported Harness config for " + name)
    if "disabled" in row and not isinstance(row["disabled"], bool):
        raise ValueError("Unsupported disabled flag for " + name)
    _assert_safe_tree(row, name, counts, set())


def _load_template() -> dict:
    """Read the bundled example and verify it still describes the official route."""
    try:
        document = yaml_codec().load(PATCH_FILE.read_text(encoding="utf-8"))
    except YAMLError:
        raise ValueError("The bundled official API patch is invalid") from None
    if not isinstance(document, list):
        raise ValueError("The bundled official API patch is invalid")
    counts = _container_counts(document)
    rows = {}
    for row in document:
        if not isinstance(row, dict):
            raise ValueError("The bundled official API patch is invalid")
        name = row.get("id")
        if name not in MANAGED_IDS or name in rows:
            raise ValueError("The bundled official API patch is invalid")
        _assert_safe_target(row, name, counts)
        rows[name] = copy.deepcopy(row)
    if set(rows) != MANAGED_IDS:
        raise ValueError("The bundled official API patch is invalid")
    native = rows["llm-deepseek"].get("config", {})
    retry = native.get("retryPolicy") if isinstance(native, dict) else None
    if (not isinstance(native, dict)
            or native.get("baseURL") != BASE_URL
            or not isinstance(retry, dict)
            or retry.get("maxRetries") != 0
            or native.get("maxTokens") != MAX_TOKENS):
        raise ValueError("The bundled official API patch is invalid")
    default = rows["agent-default-model"].get("config", {})
    log = rows["session-log-deepseek"].get("config", {})
    if (rows["session-title-llm"].get("disabled") is not True
            or not isinstance(default, dict)
            or default.get("provider") != ROUTE
            or not isinstance(default.get("model"), str)
            or not isinstance(log, dict)
            or log.get("enabled") is not False):
        raise ValueError("The bundled official API patch is invalid")
    return rows


def _merge_config(row, desired, name):
    if "config" not in row:
        row["config"] = {}
    config = row["config"]
    if not isinstance(config, dict):
        raise ValueError("Dynamic or unsupported Harness config for " + name)
    _merge_mapping(config, desired, name)


def _merge_mapping(target, desired, name):
    for key, value in desired.items():
        if isinstance(value, dict):
            if key not in target:
                target[key] = copy.deepcopy(value)
            elif not isinstance(target[key], dict):
                raise ValueError("Unsupported Harness config shape for " + name)
            else:
                _merge_mapping(target[key], value, name)
        else:
            target[key] = copy.deepcopy(value)


def patch_document(document, default_model=None, template=None):
    """Merge the official rows into a loaded profile, preserving every other row.

    ``default_model=None`` is intentional: only an explicit flag may change the
    existing model selection. The input document is not mutated.
    """
    if template is None:
        template = _load_template()
    if default_model is not None:
        if not isinstance(default_model, str) or not MODEL_PATTERN.fullmatch(default_model):
            raise ValueError("Invalid --default-model value; use a plain model id")
    if document is None:
        document = []
    if not isinstance(document, list):
        raise ValueError("Harness profile patch must be a YAML list")
    result = copy.deepcopy(document)
    counts = _container_counts(result)
    rows = {}
    for row in result:
        if not isinstance(row, dict):
            raise ValueError("Unsupported Harness profile entry")
        name = row.get("id")
        if "id" in row and not isinstance(name, str):
            raise ValueError("Unsupported Harness entry id")
        if isinstance(name, str) and name in MANAGED_IDS:
            if name in rows:
                raise ValueError("Duplicate Harness entry for " + name)
            _assert_safe_target(row, name, counts)
            rows[name] = row
            continue
        if "insert" in row:
            nested = row["insert"]
            if (set(row) != {"insert"} or not isinstance(nested, list)
                    or any(not isinstance(child, dict) or not isinstance(child.get("id"), str)
                           or "insert" in child for child in nested)):
                raise ValueError("Unsupported nested Harness patch; merge manually")
            if any(child["id"] in MANAGED_IDS for child in nested):
                raise ValueError("Nested insert of an official API entry needs manual editing")
        elif name is None:
            raise ValueError("Unsupported Harness patch operation; merge manually")

    def existing_or_new(name):
        row = rows.get(name)
        if row is None:
            row = copy.deepcopy(template[name])
            result.append(row)
            rows[name] = row
        return row

    llm = rows.get("llm-deepseek")
    if llm is None:
        # A new row already carries the bundled config.
        existing_or_new("llm-deepseek")
    else:
        # Merge the bundled keys into an existing row, retaining its other fields.
        if llm.get("disabled", False) is not False:
            raise ValueError("The official model adapter is disabled; enable it explicitly in Harness")
        _merge_config(llm, template["llm-deepseek"]["config"], "llm-deepseek")

    if default_model is not None:
        desired = copy.deepcopy(template["agent-default-model"].get("config", {}))
        desired["model"] = default_model
        row = rows.get("agent-default-model")
        if row is None:
            row = existing_or_new("agent-default-model")
            row["config"] = desired
        else:
            if row.get("disabled", False) is not False:
                raise ValueError("Default model selection is disabled; enable it explicitly in Harness")
            _merge_config(row, desired, "agent-default-model")

    title = existing_or_new("session-title-llm")
    title["disabled"] = True

    log = rows.get("session-log-deepseek")
    if log is None:
        existing_or_new("session-log-deepseek")
    else:
        _merge_config(log, template["session-log-deepseek"]["config"], "session-log-deepseek")

    return result


def _load_document(before: bytes):
    try:
        # Parser warnings can include source snippets containing credentials.
        with warnings.catch_warnings():
            warnings.simplefilter("error", YAMLWarning)
            return yaml_codec().load(before.decode("utf-8"))
    except (YAMLError, YAMLWarning, UnicodeDecodeError):
        raise ValueError("Invalid YAML in the Harness profile; nothing was changed") from None


def _check_manifest(path: Path, profile: str):
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Invalid Harness profile manifest") from None
    spec = manifest.get("dsh") if isinstance(manifest, dict) else None
    spec = spec.get("profile") if isinstance(spec, dict) else None
    bundles = spec.get("bundles") if isinstance(spec, dict) else None
    expected = [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-" + ("headless" if profile == "headless" else "web-app"),
    ]
    if bundles != expected:
        raise ValueError("Custom Harness bundle composition; follow the README instructions")


def _check_overrides(home: Path, directory: Path):
    for path in (home / "cordis.patch.yml", directory / "cordis.yml"):
        if path.is_symlink():
            raise ValueError("Refusing a symlinked Harness override file")
        if not path.exists():
            continue
        if not path.is_file():
            raise ValueError("Unsupported Harness profile override")
        try:
            value = _load_document(path.read_bytes())
        except (OSError, ValueError):
            raise ValueError("Invalid Harness profile override; nothing was changed") from None
        if value not in (None, []):
            raise ValueError("Nonempty home or root Harness overrides need manual merging")


def _no_symlink(path):
    # Check lexical ancestors before resolving: resolve() alone hides symlinks.
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Refusing a symlinked Harness path")


def _initialized_profile(home, profile: str):
    if profile not in PROFILES:
        raise ValueError("Unsupported Harness profile")
    raw = str(home)
    if not raw.strip():
        raise ValueError("Harness home is empty")
    home = Path(raw).expanduser()
    _no_symlink(home)
    lock = home / (".bridge-install-" + profile + ".lock")
    _no_symlink(lock)
    if lock.exists() and (not lock.is_file() or lock.stat().st_nlink != 1):
        raise ValueError("Unsupported Harness profile lock")
    if not home.exists() or not home.is_dir():
        raise ValueError("Harness home is not initialized")
    profiles = home / "profiles"
    if profiles.is_symlink():
        raise ValueError("Refusing a symlinked Harness profiles directory")
    if not profiles.exists() or not profiles.is_dir():
        raise ValueError("Harness profiles directory is not initialized")
    directory = profiles / profile
    if directory.is_symlink():
        raise ValueError("Refusing a symlinked Harness profile directory")
    if not directory.exists() or not directory.is_dir():
        raise ValueError("Harness profile is not initialized; create it in Harness first")
    for name in ("package.json", "cordis.patch.yml", "cordis.yml"):
        if (directory / name).is_symlink():
            raise ValueError("Refusing a symlinked Harness profile file")
    if (home / "cordis.patch.yml").is_symlink():
        raise ValueError("Refusing a symlinked Harness override file")
    manifest = directory / "package.json"
    patch = directory / "cordis.patch.yml"
    if not manifest.is_file() or not patch.is_file():
        raise ValueError("Harness profile is not initialized; create it in Harness first")
    _check_manifest(manifest, profile)
    _check_overrides(home, directory)
    return home, directory


@contextmanager
def _profile_lock(home, profile):
    """Use the shared installer lock without following a symlink or hard link."""
    path = home / (".bridge-install-" + profile + ".lock")
    _no_symlink(path)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        named = path.lstat()
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or stat.S_ISLNK(named.st_mode)
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            raise ValueError("Unsafe Harness profile lock")
        os.chmod(path, 0o600) if os.name == "nt" else os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "r+") as lock:
            descriptor = None
            _file_lock(lock)
            try:
                yield
            finally:
                _file_lock(lock, unlock=True)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def plan_install(home, profile: str, default_model=None) -> Plan:
    """Validate and render an update without writing anything."""
    _, directory = _initialized_profile(home, profile)
    path = directory / "cordis.patch.yml"
    before = path.read_bytes()
    document = _load_document(before)
    template = _load_template()
    patched = patch_document(document, default_model, template=template)
    data = render(patched)
    return Plan(path=path, before=before, patched=data, changed=data != before)


def install(home, profile: str, default_model=None, dry_run=False):
    """Apply the update atomically; return ``(profile_path, backup_or_none)``.

    An unchanged profile is left alone: no lock, no backup and no write. A dry
    run performs the same validation and rendering but never writes.
    """
    plan = plan_install(home, profile, default_model)
    if dry_run or not plan.changed:
        return plan.path, None
    home_path = Path(str(home)).expanduser()
    with _profile_lock(home_path, profile):
        current = plan_install(home_path, profile, default_model)
        if not current.changed:
            return current.path, None
        return current.path, write_private(current.path, current.patched, current.before)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", required=True, choices=PROFILES, metavar="PROFILE",
                        help="Install into an already initialized desktop, web or headless profile")
    parser.add_argument("--home", type=Path, default=default_home(),
                        help="Harness home (default: DSH_HOME or ~/.dsh)")
    parser.add_argument("--default-model",
                        help="Explicitly select the official model for new sessions")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and show the target without writing anything")
    args = parser.parse_args(argv)
    try:
        plan = plan_install(args.home, args.install, args.default_model)
        if args.dry_run:
            if plan.changed:
                print("Dry run: would update " + str(plan.path))
            else:
                print("Dry run: no changes to " + str(plan.path))
            print("No files were written. Default model selection was left unchanged "
                  "unless --default-model was passed.")
            return
        path, backup = install(args.home, args.install, args.default_model)
        if backup is None:
            print("No changes: " + str(path))
        else:
            print("Harness official API configuration: " + str(path))
            print("Previous configuration: " + str(backup))
        print("Enter the API key only in Harness Settings -> Models -> DeepSeek; "
              "no key was read.")
        print("No network requests sent. Automatic retries are disabled in this profile.")
    except YAMLError:
        parser.exit(1, "Harness official API setup failed: invalid YAML; nothing was changed\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, "Harness official API setup failed: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
