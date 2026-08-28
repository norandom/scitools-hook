"""Effective settings: precedence merge, provenance and located errors (req 3.2, 3.8, 3.10).

Layers, lowest precedence first: built-in defaults, the user file
(``$XDG_CONFIG_HOME/scitools-hook/config.toml``, else ``~/.config/...``), the repository file
(``scitools-hook.toml``, replaced by an explicit ``--config`` path that must exist),
``SCITOOLS_HOOK_*`` environment variables and command-line overrides. Tables are deep-merged so
a layer overrides only the keys it defines; lists are replaced wholesale. Every leaf gets a
provenance entry (``default``, ``user:<path>``, ``repo:<path>``, ``env:<VAR>``, ``cli``), a
threshold as one entry under ``thresholds.<scope>.<metric>``. A bare number where a limit is
expected means ``{max = number}``, so it overrides the maximum and keeps the severity.

Environment names are ``SCITOOLS_HOOK_<SECTION>__<KEY>`` with ``__`` between path segments:
``SCITOOLS_HOOK_UNDERSTAND__HOME`` -> ``understand.home``, ``SCITOOLS_HOOK_RATCHET__STRICT=1``
-> ``ratchet.strict``. Segments are lower-cased, except the metric under ``thresholds`` and the
rule under ``hints`` which keep their case (``SCITOOLS_HOOK_THRESHOLDS__routine__CountPath``).
Names without ``__`` are not settings (``SCITOOLS_HOOK_SKIP``, ``SCITOOLS_HOOK_SOFT_FAIL``, ...)
and are ignored. A value is read as a TOML scalar and falls back to the plain string, so
``true``, ``1``, ``["a"]`` and ``/opt/scitools`` all behave; a value that starts like a TOML
literal (quote, ``[``, ``{``) must parse.

Command-line overrides are dotted keys (``{"structure.depth": 5}``); a ``None`` value is ignored
so a CLI layer can pass its unset options straight through, and the reserved key ``config``
carries the explicit configuration file instead of a setting.

Every failure — unreadable or malformed file, unknown key, scope or metric name, wrong type,
invalid regular expression — is a ``ConfigError`` naming the file and the dotted key (req 3.8).
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.metric_names import SCOPES, is_valid_scope
from scitools_hook.config.models import Provenance, Settings, ThresholdSpec
from scitools_hook.config.models_validation import is_number, threshold_entries
from scitools_hook.config.template import CONFIG_FILENAME
from scitools_hook.config.validate import validate_settings
from scitools_hook.errors import ConfigError

Layer = dict[str, object]
"""One configuration source in TOML shape: nested tables, thresholds still grouped by scope."""

ENV_PREFIX: Final = "SCITOOLS_HOOK_"
ENV_SEPARATOR: Final = "__"
CLI_CONFIG_KEY: Final = "config"
"""Reserved command-line key holding an explicit configuration file (not a setting)."""

USER_CONFIG_RELPATH: Final = Path("scitools-hook") / "config.toml"

_CASE_SENSITIVE_ROOTS: Final[dict[str, int]] = {"thresholds": 2, "hints": 1}
"""Section -> number of leading environment segments that are lower-cased (the rest keep case)."""

_TOML_LITERALS: Final = ('"', "'", "[", "{")
_PYDANTIC_HINTS: Final[dict[str, str]] = {
    "extra_forbidden": "remove the key or correct its spelling",
    "missing": "the key is required",
}


@dataclass(frozen=True, slots=True)
class _Source:
    """One layer's provenance label and, for file layers, the file it was read from."""

    label: str
    file: Path | None = None


def user_config_path(env: Mapping[str, str]) -> Path:
    """The user-level configuration file, honouring ``XDG_CONFIG_HOME`` (req 3.2)."""
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / USER_CONFIG_RELPATH


def repo_config_path(repo_root: Path) -> Path:
    """The repository-level configuration file inside ``repo_root``."""
    return repo_root / CONFIG_FILENAME


def load_settings(
    repo_root: Path | None, cli_overrides: dict[str, object], env: Mapping[str, str]
) -> tuple[Settings, Provenance]:
    """Merge every configuration layer into validated ``Settings`` and its ``Provenance``.

    ``repo_root`` may be ``None`` outside a repository; ``cli_overrides`` maps dotted keys to
    values (plus the reserved ``config`` key); ``env`` is the process environment. Raises
    ``ConfigError`` with the offending file and dotted key for any invalid input (req 3.8).
    """
    merged: Layer = {}
    values: dict[str, str] = {}
    for source, layer in _layers(repo_root, cli_overrides, env):
        _Merger(values, source).apply(merged, layer)
    return _build(merged, values), Provenance(values=values)


def source_of(provenance: Provenance, key: str) -> str:
    """The provenance label of ``key``, of its nearest known parent, else ``default``."""
    return _source_label(provenance.values, key)


def attach_source(error: ConfigError, provenance: Provenance) -> ConfigError:
    """Return ``error`` with ``file`` filled in from ``provenance`` when it names only a key.

    Lets a later caller (the metric-catalogue validation, which runs once Understand is
    available) report the file a rejected value came from (req 3.8, 3.10).
    """
    return _relocated(error, provenance.values)


# --- layers ----------------------------------------------------------------------


def _layers(
    repo_root: Path | None, cli_overrides: Mapping[str, object], env: Mapping[str, str]
) -> list[tuple[_Source, Mapping[str, object]]]:
    """Every configuration source in increasing precedence."""
    layers: list[tuple[_Source, Mapping[str, object]]] = [(_Source("default"), _defaults_layer())]
    layers.extend(_file_layers(repo_root, cli_overrides, env))
    layers.extend(_env_layers(env))
    layers.extend(_cli_layers(cli_overrides))
    return layers


def _defaults_layer() -> Layer:
    """The built-in defaults in TOML shape (thresholds regrouped into their scope tables)."""
    settings = default_settings()
    layer: Layer = settings.model_dump()
    layer["thresholds"] = _threshold_tables(settings.thresholds)
    return layer


def _threshold_tables(specs: Sequence[ThresholdSpec]) -> dict[str, dict[str, object]]:
    """Turn flattened specs back into ``{scope: {metric: {max, min, severity, ratchet}}}``."""
    tables: dict[str, dict[str, object]] = {}
    for spec in specs:
        entry: dict[str, object] = {"severity": spec.severity, "ratchet": spec.ratchet}
        entry.update(spec.limit.model_dump(exclude_none=True))
        tables.setdefault(spec.scope, {})[spec.metric] = entry
    return tables


def _file_layers(
    repo_root: Path | None, cli_overrides: Mapping[str, object], env: Mapping[str, str]
) -> list[tuple[_Source, Mapping[str, object]]]:
    """The user file and then the repository (or explicit ``--config``) file, when they exist."""
    layers: list[tuple[_Source, Mapping[str, object]]] = []
    user = user_config_path(env)
    user_data = _read_toml(user, required=False)
    if user_data is not None:
        layers.append((_Source(f"user:{user}", user), user_data))
    path, required = _repo_config(repo_root, cli_overrides)
    if path is None:
        return layers
    repo_data = _read_toml(path, required=required)
    if repo_data is not None:
        layers.append((_Source(f"repo:{path}", path), repo_data))
    return layers


def _repo_config(
    repo_root: Path | None, cli_overrides: Mapping[str, object]
) -> tuple[Path | None, bool]:
    """The repository-level file and whether it must exist (an explicit ``--config`` must)."""
    explicit = cli_overrides.get(CLI_CONFIG_KEY)
    if explicit is not None:
        return Path(str(explicit)), True
    return (repo_config_path(repo_root) if repo_root is not None else None), False


def _read_toml(path: Path, *, required: bool) -> Layer | None:
    """Parse ``path``; ``None`` when it is absent and optional (req 3.1)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as err:
        if required:
            raise ConfigError(
                f"configuration file {path} does not exist", file=path, hint="check --config"
            ) from err
        return None
    except OSError as err:
        raise ConfigError(f"cannot read configuration file {path}: {err}", file=path) from err
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(
            f"{path} is not valid TOML: {err}", file=path, hint="see https://toml.io"
        ) from err


def _env_layers(env: Mapping[str, str]) -> list[tuple[_Source, Mapping[str, object]]]:
    """One layer per ``SCITOOLS_HOOK_*__*`` variable, in sorted name order."""
    layers: list[tuple[_Source, Mapping[str, object]]] = []
    for name in sorted(env):
        parts = _env_path(name)
        if parts is None:
            continue
        value = _env_value(name, env[name], ".".join(parts))
        layers.append((_Source(f"env:{name}"), _nest(parts, value)))
    return layers


def _env_path(name: str) -> list[str] | None:
    """Map ``SCITOOLS_HOOK_A__B`` to ``["a", "b"]``; ``None`` when the name is not a setting."""
    if not name.startswith(ENV_PREFIX):
        return None
    tail = name[len(ENV_PREFIX) :]
    if ENV_SEPARATOR not in tail:
        return None
    parts = tail.split(ENV_SEPARATOR)
    lowered = _CASE_SENSITIVE_ROOTS.get(parts[0].lower(), len(parts))
    return [part.lower() for part in parts[:lowered]] + parts[lowered:]


def _env_value(name: str, raw: str, key: str) -> object:
    """Read one environment value as a TOML scalar, falling back to the plain string."""
    text = raw.strip()
    try:
        return tomllib.loads(f"value = {text}")["value"]
    except tomllib.TOMLDecodeError as err:
        if text.startswith(_TOML_LITERALS):
            raise ConfigError(
                f"{name}: {raw!r} is not a valid TOML value ({err})",
                key=key,
                hint='quote strings as "text" and write lists as ["a", "b"]',
            ) from err
        return raw


def _cli_layers(
    cli_overrides: Mapping[str, object],
) -> list[tuple[_Source, Mapping[str, object]]]:
    """One layer per dotted override; a ``None`` value and the ``config`` key are skipped."""
    return [
        (_Source("cli"), _nest(key.split("."), value))
        for key, value in cli_overrides.items()
        if key != CLI_CONFIG_KEY and value is not None
    ]


def _nest(parts: Sequence[str], value: object) -> Layer:
    """Build the nested mapping ``{a: {b: value}}`` for the key path ``a.b``."""
    layer: Layer = {}
    cursor = layer
    for part in parts[:-1]:
        child: Layer = {}
        cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value
    return layer


# --- merge and provenance --------------------------------------------------------


def _is_limit(path: str) -> bool:
    """True for ``thresholds.<scope>.<metric>`` and ``structure.fan.<key>``: one limit value."""
    parent, _, _ = path.rpartition(".")
    return parent == "structure.fan" or (
        parent.startswith("thresholds.") and parent.count(".") == 1
    )


def _as_limit_table(current: object, raw: object, path: str) -> object:
    """A bare number where a limit lives means ``{max = number}``, so it keeps the severity."""
    if is_number(raw) and isinstance(current, Mapping) and _is_limit(path):
        return {"max": raw}
    return raw


class _Merger:
    """Applies one layer onto the merged mapping and records where every leaf came from."""

    def __init__(self, values: dict[str, str], source: _Source) -> None:
        self.values = values
        self.source = source

    def apply(self, base: Layer, overlay: Mapping[str, object], prefix: str = "") -> None:
        """Deep-merge ``overlay`` into ``base``; lists and limits replace, tables merge."""
        for name, raw in overlay.items():
            path = f"{prefix}.{name}" if prefix else name
            current = base.get(name)
            value = _as_limit_table(current, raw, path)
            if not isinstance(current, dict) or not isinstance(value, Mapping):
                base[name] = deepcopy(value)
                self._record(path, value)
            elif _is_limit(path):
                base[name] = {**current, **value}
                self.values[path] = self.source.label
            else:
                self.apply(current, value, path)

    def _record(self, path: str, value: object) -> None:
        """Label every leaf of ``value``; a limit and an empty table are leaves themselves."""
        if isinstance(value, Mapping) and value and not _is_limit(path):
            for name, item in value.items():
                self._record(f"{path}.{name}", item)
            return
        self.values[path] = self.source.label


def _source_label(values: Mapping[str, str], key: str) -> str:
    """The label of ``key``, of its nearest parent, of any child below it, else ``default``."""
    base = key.split("[", 1)[0]
    parts = base.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in values:
            return values[candidate]
        parts.pop()
    prefix = f"{base}."
    return next((label for name, label in values.items() if name.startswith(prefix)), "default")


def _file_of(values: Mapping[str, str], key: str) -> Path | None:
    """The configuration file behind ``key``; ``None`` for defaults, environment and CLI."""
    kind, _, location = _source_label(values, key).partition(":")
    return Path(location) if location and kind in {"user", "repo"} else None


def _error(
    message: str, key: str, values: Mapping[str, str], hint: str | None = None
) -> ConfigError:
    """A ``ConfigError`` naming the dotted key and the file that last set it (req 3.8)."""
    return ConfigError(message, file=_file_of(values, key), key=key, hint=hint)


def _relocated(error: ConfigError, values: Mapping[str, str]) -> ConfigError:
    if error.file is not None or error.key is None:
        return error
    return ConfigError(
        error.message, file=_file_of(values, error.key), key=error.key, hint=error.hint
    )


# --- validation ------------------------------------------------------------------


def _build(merged: Layer, values: Mapping[str, str]) -> Settings:
    """Validate the merged layer into ``Settings``, mapping every failure onto a file and key."""
    data = dict(merged)
    entries, keys = _threshold_specs(data.pop("thresholds", {}), values)
    data["thresholds"] = entries
    try:
        settings = Settings.model_validate(data)
    except ValidationError as err:
        raise _from_validation_error(err, keys, values) from err
    try:
        validate_settings(settings, None)
    except ConfigError as err:
        located = _relocated(err, values)
        if located is err:
            raise
        raise located from err
    return settings


def _threshold_specs(
    tables: object, values: Mapping[str, str]
) -> tuple[list[dict[str, object]], list[str]]:
    """Flatten the threshold tables, keeping one dotted key per entry for error reporting."""
    if not isinstance(tables, Mapping):
        raise _error("thresholds must be [thresholds.<scope>] tables", "thresholds", values)
    entries: list[dict[str, object]] = []
    keys: list[str] = []
    for scope, table in tables.items():
        scope_entries, scope_keys = _scope_specs(str(scope), table, values)
        entries.extend(scope_entries)
        keys.extend(scope_keys)
    return entries, keys


def _scope_specs(
    scope: str, table: object, values: Mapping[str, str]
) -> tuple[list[dict[str, object]], list[str]]:
    """One scope table; every entry is validated on its own so its key can be named."""
    key = f"thresholds.{scope}"
    if not is_valid_scope(scope):
        hint = f"expected one of {', '.join(SCOPES)}"
        raise _error(f"{key}: unknown threshold scope {scope!r}", key, values, hint)
    if not isinstance(table, Mapping):
        raise _error(f"[{key}] must be a table of 'Metric = limit' entries", key, values)
    entries: list[dict[str, object]] = []
    keys: list[str] = []
    for metric, raw in table.items():
        entry_key = f"{key}.{metric}"
        try:
            entries.extend(threshold_entries({scope: {str(metric): raw}}))
        except ValueError as err:
            raise _error(str(err), entry_key, values) from err
        keys.append(entry_key)
    return entries, keys


def _from_validation_error(
    error: ValidationError, keys: Sequence[str], values: Mapping[str, str]
) -> ConfigError:
    """Map the first pydantic failure onto a ``ConfigError`` with a dotted key (req 3.8)."""
    detail = error.errors()[0]
    key = _error_key(detail["loc"], keys)
    return _error(f"{key}: {detail['msg']}", key, values, _PYDANTIC_HINTS.get(detail["type"]))


def _error_key(loc: Sequence[int | str], keys: Sequence[str]) -> str:
    """Turn a pydantic location into a dotted key; a threshold index maps back to its metric."""
    if len(loc) > 1 and loc[0] == "thresholds" and isinstance(loc[1], int):
        return keys[loc[1]] if loc[1] < len(keys) else "thresholds"
    key = ""
    for part in loc:
        if isinstance(part, int):
            key = f"{key}[{part}]"
        else:
            key = f"{key}.{part}" if key else str(part)
    return key or "settings"
