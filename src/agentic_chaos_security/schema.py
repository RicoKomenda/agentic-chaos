"""The experiment file format: validation with readable errors, and a generated JSON Schema.

Both are derived from the fault and probe registries (constructor and factory signatures), so they can
never drift from the code. The CLI commands ``validate`` and ``schema`` expose them.

Format versions:

* ``agentic-chaos/v1`` - current, stable. Changes within v1 are additive only.
* ``agentic-chaos/v1alpha1`` - accepted with a :class:`DeprecationWarning`; identical structure.
"""

from __future__ import annotations

import difflib
import inspect
import types
import typing
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agentic_chaos_security import faults as fault_lib
from agentic_chaos_security import probes as probe_lib
from agentic_chaos_security.runtime import POINTS

__all__ = [
    "API_VERSION",
    "DEPRECATED_VERSIONS",
    "Param",
    "ValidationError",
    "fault_params",
    "json_schema",
    "probe_params",
    "validate",
]

API_VERSION = "agentic-chaos/v1"
DEPRECATED_VERSIONS = ("agentic-chaos/v1alpha1",)
KINDS = ("Experiment", "McpProxy")
FAULT_OPTIONS = ("target", "point", "probability", "after_calls", "max_injections")

_TOP = ("apiVersion", "kind", "metadata", "spec")
_METADATA = ("name", "description", "tags")
_EXPERIMENT_SPEC = (
    "hypothesis",
    "target",
    "runs",
    "seed",
    "baseline",
    "pass_rate",
    "confidence",
    "require_confidence",
    "faults",
    "probes",
    "detection",
)
_PROXY_SPEC = ("seed", "faults", "probes")
_TARGET = ("entrypoint", "args")
_FAULT_ITEM = ("type", *FAULT_OPTIONS, "params")
_PROBE_ITEM = ("type", "params", "min_pass_rate")


class ValidationError(ValueError):
    """An experiment file does not match the format. ``errors`` lists every problem found."""

    def __init__(self, errors: list[str], source: str = "") -> None:
        self.errors = errors
        self.source = source
        prefix = f"{source}: " if source else ""
        super().__init__("\n".join(f"{prefix}{e}" for e in errors))


@dataclass(frozen=True)
class Param:
    name: str
    json_types: frozenset[str]
    required: bool
    default: Any = None


# --- signature introspection -------------------------------------------------------------------


def _json_types(annotation: Any) -> frozenset[str]:
    """Map a Python annotation onto JSON Schema type names (empty set = any)."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        result: set[str] = set()
        for arg in typing.get_args(annotation):
            sub = _json_types(arg)
            if not sub:
                return frozenset()
            result |= sub
        return frozenset(result)
    base = origin or annotation
    if base is type(None):
        return frozenset({"null"})
    if base is bool:
        return frozenset({"boolean"})
    if base is int:
        return frozenset({"integer"})
    if base is float:
        return frozenset({"number"})
    if base is str:
        return frozenset({"string"})
    if base in (dict, Mapping):
        return frozenset({"object"})
    if base in (list, tuple):
        return frozenset({"array"})
    if base is Callable or getattr(base, "__name__", "") == "Callable":
        return frozenset({"string"})  # a "module:callable" entrypoint in files
    return frozenset()


def _params(fn: Callable[..., Any], skip: tuple[str, ...] = ()) -> dict[str, Param]:
    hints = typing.get_type_hints(fn)
    result: dict[str, Param] = {}
    for name, parameter in inspect.signature(fn).parameters.items():
        if name in ("self", *skip) or parameter.kind in (parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL):
            continue
        required = parameter.default is inspect.Parameter.empty
        default = None if required else parameter.default
        result[name] = Param(name, _json_types(hints.get(name, Any)), required, default)
    return result


def fault_params(cls: type[fault_lib.Fault]) -> dict[str, Param]:
    """Fault-specific parameters (``params:`` in files), collected along the class hierarchy."""
    result: dict[str, Param] = {}
    for klass in reversed(cls.__mro__):
        if issubclass(klass, fault_lib.Fault) and klass is not fault_lib.Fault and "__init__" in vars(klass):
            result.update(_params(vars(klass)["__init__"], skip=FAULT_OPTIONS))
    return result


def probe_params(factory: Callable[..., Any]) -> dict[str, Param]:
    return _params(factory)


# --- validation ----------------------------------------------------------------------------------


def _suggest(value: str, options: typing.Iterable[str]) -> str:
    close = difflib.get_close_matches(value, list(options), n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


def _type_ok(value: Any, allowed: frozenset[str]) -> bool:
    if not allowed:
        return True
    checks = {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
    }
    return any(checks[t] for t in allowed)


class _Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def mapping(self, value: Any, path: str, allowed: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(value, dict):
            self.error(path, f"expected a mapping, got {type(value).__name__}")
            return {}
        for key in value:
            if key not in allowed:
                self.error(f"{path}.{key}", f"unknown field{_suggest(str(key), allowed)}")
        return value

    def typed(self, value: Any, path: str, allowed: frozenset[str], what: str = "") -> bool:
        if not _type_ok(value, allowed):
            expected = " or ".join(sorted(allowed))
            self.error(path, f"expected {what or expected}, got {type(value).__name__} {value!r}")
            return False
        return True

    def number(self, value: Any, path: str, low: float, high: float, integer: bool = False) -> None:
        kind = frozenset({"integer"}) if integer else frozenset({"number"})
        if self.typed(value, path, kind) and not low <= value <= high:
            self.error(path, f"must be between {low} and {high}, got {value}")

    def params(self, value: Any, path: str, signature: dict[str, Param], owner: str) -> None:
        if value is None:
            value = {}
        if not isinstance(value, dict):
            self.error(path, "expected a mapping of parameters")
            return
        for key, item in value.items():
            if key not in signature:
                known = ", ".join(sorted(signature)) or "none"
                hint = _suggest(str(key), signature) or f" (known: {known})"
                self.error(f"{path}.{key}", f"unknown parameter for {owner}{hint}")
            else:
                self.typed(item, f"{path}.{key}", signature[key].json_types)
        for name, param in signature.items():
            if param.required and name not in value:
                self.error(path, f"missing required parameter {name!r} for {owner}")

    def fault(self, item: Any, path: str) -> None:
        item = self.mapping(item, path, _FAULT_ITEM)
        kind = item.get("type")
        if not isinstance(kind, str):
            self.error(f"{path}.type", "missing fault type")
            return
        cls = fault_lib.FAULTS.get(kind)
        if cls is None:
            self.error(f"{path}.type", f"unknown fault {kind!r}{_suggest(kind, fault_lib.FAULTS)}")
            return
        if "target" in item:
            self.typed(item["target"], f"{path}.target", frozenset({"string"}), "a glob string")
        if "point" in item:
            points = item["point"] if isinstance(item["point"], list) else [item["point"]]
            for point in points:
                if point not in POINTS:
                    self.error(f"{path}.point", f"unknown injection point {point!r}{_suggest(str(point), POINTS)}")
        if "probability" in item:
            self.number(item["probability"], f"{path}.probability", 0, 1)
        if "after_calls" in item:
            self.number(item["after_calls"], f"{path}.after_calls", 0, float("inf"), integer=True)
        if item.get("max_injections") is not None:
            self.number(item["max_injections"], f"{path}.max_injections", 0, float("inf"), integer=True)
        self.params(item.get("params"), f"{path}.params", fault_params(cls), f"fault {kind!r}")

    def probe(self, item: Any, path: str) -> None:
        item = self.mapping(item, path, _PROBE_ITEM)
        kind = item.get("type")
        if not isinstance(kind, str):
            self.error(f"{path}.type", "missing probe type")
            return
        factory = probe_lib.PROBES.get(kind)
        if factory is None:
            self.error(f"{path}.type", f"unknown probe {kind!r}{_suggest(kind, probe_lib.PROBES)}")
            return
        if "min_pass_rate" in item:
            self.number(item["min_pass_rate"], f"{path}.min_pass_rate", 0, 1)
        self.params(item.get("params"), f"{path}.params", probe_params(factory), f"probe {kind!r}")

    def items(self, value: Any, path: str, check: Callable[[Any, str], None]) -> None:
        if value is None:
            return
        if not isinstance(value, list):
            self.error(path, "expected a list")
            return
        for index, item in enumerate(value):
            check(item, f"{path}[{index}]")


def validate(doc: Any, *, entrypoint_override: bool = False) -> list[str]:
    """Return every problem in an experiment or proxy document (empty list = valid).

    Deprecated ``apiVersion`` values emit a :class:`DeprecationWarning` but are not errors.
    """
    check = _Checker()
    doc = check.mapping(doc, "document", _TOP)
    if not doc:
        return check.errors
    version = doc.get("apiVersion")
    if version in DEPRECATED_VERSIONS:
        warnings.warn(
            f"apiVersion {version!r} is deprecated; use {API_VERSION!r} (the structure is unchanged)",
            DeprecationWarning,
            stacklevel=3,
        )
    elif version != API_VERSION:
        check.error("apiVersion", f"expected {API_VERSION!r}, got {version!r}")
    kind = doc.get("kind")
    if kind not in KINDS:
        check.error("kind", f"expected one of {', '.join(KINDS)}, got {kind!r}")
        return check.errors

    if kind == "Experiment":
        meta = check.mapping(doc.get("metadata"), "metadata", _METADATA)
        if not isinstance(meta.get("name"), str) or not meta.get("name"):
            check.error("metadata.name", "required, a non-empty string")
        if "tags" in meta and not (isinstance(meta["tags"], list) and all(isinstance(t, str) for t in meta["tags"])):
            check.error("metadata.tags", "expected a list of strings")
    elif "metadata" in doc:
        check.mapping(doc["metadata"], "metadata", _METADATA)

    spec = check.mapping(doc.get("spec", {}), "spec", _EXPERIMENT_SPEC if kind == "Experiment" else _PROXY_SPEC)
    if kind == "Experiment":
        target = check.mapping(spec.get("target", {}), "spec.target", _TARGET)
        entrypoint = target.get("entrypoint")
        if entrypoint is None and not entrypoint_override:
            check.error("spec.target.entrypoint", "required (or pass --target)")
        elif entrypoint is not None and (not isinstance(entrypoint, str) or ":" not in entrypoint):
            check.error("spec.target.entrypoint", f"expected 'package.module:callable', got {entrypoint!r}")
        if "args" in target and not isinstance(target["args"], dict):
            check.error("spec.target.args", "expected a mapping of keyword arguments")
        if "runs" in spec:
            check.number(spec["runs"], "spec.runs", 1, float("inf"), integer=True)
        for key in ("baseline", "require_confidence"):
            if key in spec:
                check.typed(spec[key], f"spec.{key}", frozenset({"boolean"}))
        if "pass_rate" in spec:
            check.number(spec["pass_rate"], "spec.pass_rate", 0, 1)
        if "confidence" in spec:
            check.number(spec["confidence"], "spec.confidence", 0.5, 0.999)
        if "hypothesis" in spec:
            check.typed(spec["hypothesis"], "spec.hypothesis", frozenset({"string"}))
        check.items(spec.get("detection"), "spec.detection", check.probe)
        if not spec.get("faults"):
            check.error("spec.faults", "at least one fault is required")
    if spec.get("seed") is not None:
        check.typed(spec["seed"], "spec.seed", frozenset({"integer"}))
    check.items(spec.get("faults"), "spec.faults", check.fault)
    check.items(spec.get("probes"), "spec.probes", check.probe)
    return check.errors


# --- JSON Schema -------------------------------------------------------------------------------


def _property(param: Param) -> dict[str, Any]:
    prop: dict[str, Any] = {}
    if param.json_types:
        names = sorted(param.json_types)
        prop["type"] = names[0] if len(names) == 1 else names
    if not param.required and param.default is not None and not isinstance(param.default, (tuple, set)):
        prop["default"] = param.default
    return prop


def _params_schema(signature: dict[str, Param]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {name: _property(p) for name, p in signature.items()},
        "additionalProperties": False,
    }
    required = [name for name, p in signature.items() if p.required]
    if required:
        schema["required"] = required
    return schema


def _builtin(obj: Any) -> bool:
    return str(getattr(obj, "__module__", "")).startswith("agentic_chaos_security.")


def json_schema(*, include_extensions: bool = False) -> dict[str, Any]:
    """A JSON Schema (draft 2020-12) for experiment and MCP proxy files, generated from the registries.

    By default only the built-in faults and probes are described; ``include_extensions`` adds those
    registered by other packages at runtime.
    """
    point = {"enum": list(POINTS)}
    fault_variants = []
    faults = {k: v for k, v in fault_lib.FAULTS.items() if include_extensions or _builtin(v)}
    probes = {k: v for k, v in probe_lib.PROBES.items() if include_extensions or _builtin(v)}
    for kind, cls in sorted(faults.items()):
        fault_variants.append(
            {
                "type": "object",
                "description": (inspect.getdoc(cls) or kind).splitlines()[0],
                "properties": {
                    "type": {"const": kind},
                    "target": {"type": "string", "default": "*"},
                    "point": {"oneOf": [point, {"type": "array", "items": point}]},
                    "probability": {"type": "number", "minimum": 0, "maximum": 1, "default": 1.0},
                    "after_calls": {"type": "integer", "minimum": 0, "default": 0},
                    "max_injections": {"type": ["integer", "null"], "minimum": 0},
                    "params": _params_schema(fault_params(cls)),
                },
                "required": ["type"],
                "additionalProperties": False,
            }
        )
    probe_variants = []
    for kind, factory in sorted(probes.items()):
        probe_variants.append(
            {
                "type": "object",
                "description": (inspect.getdoc(factory) or kind).splitlines()[0],
                "properties": {
                    "type": {"const": kind},
                    "params": _params_schema(probe_params(factory)),
                    "min_pass_rate": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["type"],
                "additionalProperties": False,
            }
        )
    metadata = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }
    versions = {"enum": [API_VERSION, *DEPRECATED_VERSIONS]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/RicoKomenda/agentic-chaos/schema/agentic-chaos.v1.schema.json",
        "title": "Agentic Chaos experiment or MCP proxy file",
        "$defs": {
            "fault": {"oneOf": fault_variants},
            "probe": {"oneOf": probe_variants},
            "metadata": metadata,
        },
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "apiVersion": versions,
                    "kind": {"const": "Experiment"},
                    "metadata": {"$ref": "#/$defs/metadata", "required": ["name"]},
                    "spec": {
                        "type": "object",
                        "properties": {
                            "hypothesis": {"type": "string"},
                            "target": {
                                "type": "object",
                                "properties": {
                                    "entrypoint": {"type": "string", "pattern": "^[\\w.]+:[\\w.]+$"},
                                    "args": {"type": "object"},
                                },
                                "additionalProperties": False,
                            },
                            "runs": {"type": "integer", "minimum": 1, "default": 1},
                            "seed": {"type": ["integer", "null"], "default": 0},
                            "baseline": {"type": "boolean", "default": True},
                            "pass_rate": {"type": "number", "minimum": 0, "maximum": 1, "default": 1.0},
                            "confidence": {"type": "number", "minimum": 0.5, "maximum": 0.999, "default": 0.95},
                            "require_confidence": {"type": "boolean", "default": False},
                            "faults": {"type": "array", "items": {"$ref": "#/$defs/fault"}, "minItems": 1},
                            "probes": {"type": "array", "items": {"$ref": "#/$defs/probe"}},
                            "detection": {"type": "array", "items": {"$ref": "#/$defs/probe"}},
                        },
                        "required": ["faults"],
                        "additionalProperties": False,
                    },
                },
                "required": ["apiVersion", "kind", "metadata", "spec"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "apiVersion": versions,
                    "kind": {"const": "McpProxy"},
                    "metadata": {"$ref": "#/$defs/metadata"},
                    "spec": {
                        "type": "object",
                        "properties": {
                            "seed": {"type": ["integer", "null"]},
                            "faults": {"type": "array", "items": {"$ref": "#/$defs/fault"}},
                            "probes": {"type": "array", "items": {"$ref": "#/$defs/probe"}},
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["apiVersion", "kind", "spec"],
                "additionalProperties": False,
            },
        ],
    }
