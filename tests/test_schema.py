import json
import warnings
from pathlib import Path

import jsonschema
import pytest
import yaml

from agentic_chaos import loader, schema
from agentic_chaos.cli import main

ROOT = Path(__file__).parent.parent
SCHEMA_FILE = ROOT / "schema/agentic-chaos.v1.schema.json"
FILES = sorted(ROOT.glob("experiments/**/*.yaml"))


def doc(**spec) -> dict:
    base = {"target": {"entrypoint": "examples.mailbot.agent:naive"}, "faults": [{"type": "timeout"}]}
    return {"apiVersion": schema.API_VERSION, "kind": "Experiment", "metadata": {"name": "t"}, "spec": {**base, **spec}}


def test_committed_schema_is_up_to_date():
    """Regenerate with: agentic-chaos schema --output schema/agentic-chaos.v1.schema.json"""
    assert json.loads(SCHEMA_FILE.read_text()) == schema.json_schema()


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_catalog_matches_json_schema(path):
    jsonschema.validate(yaml.safe_load(path.read_text()), schema.json_schema())


def test_json_schema_rejects_what_the_validator_rejects():
    bad = doc(faults=[{"type": "inject_instruction", "params": {"payloadd": "x"}}])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema.json_schema())
    assert schema.validate(bad)


def test_valid_document_has_no_errors():
    assert schema.validate(doc()) == []


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"runz": 3}, "spec.runz: unknown field (did you mean 'runs'?)"),
        ({"faults": [{"type": "timeot"}]}, "unknown fault 'timeot' (did you mean 'timeout'?)"),
        ({"faults": [{"type": "timeout", "point": "tool.cal"}]}, "did you mean 'tool.call'?"),
        ({"faults": [{"type": "flood", "params": {"size": "big"}}]}, "params.size: expected integer"),
        ({"faults": []}, "spec.faults: at least one fault is required"),
        ({"probes": [{"type": "max_tool_calls"}]}, "missing required parameter 'limit'"),
        ({"probes": [{"type": "not_refused", "min_pass_rate": 1.5}]}, "must be between 0 and 1"),
        ({"runs": 0}, "spec.runs: must be between 1"),
        ({"target": {"entrypoint": "no_colon"}}, "expected 'package.module:callable'"),
    ],
)
def test_validation_messages(spec, message):
    errors = schema.validate(doc(**spec))
    assert any(message in e for e in errors), errors


def test_deprecated_version_warns_but_loads():
    old = {**doc(), "apiVersion": "agentic-chaos/v1alpha1"}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert loader.from_dict(old).name == "t"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_unknown_version_is_an_error():
    assert "apiVersion" in schema.validate({**doc(), "apiVersion": "agentic-chaos/v9"})[0]


def test_yaml_syntax_error_has_location(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("apiVersion: agentic-chaos/v1\nkind: [unclosed\n")
    with pytest.raises(loader.ValidationError) as info:
        loader.load(path)
    assert "line 3" in str(info.value) and str(path) in str(info.value)


def test_probes_registered_by_the_target_module_are_known(tmp_path):
    custom = doc(probes=[{"type": "always_fine"}])
    custom["spec"]["target"]["entrypoint"] = "tests.custom_probe_target:run"
    assert loader.from_dict(custom).probes


def test_cli_validate_and_exit_codes(tmp_path, capsys):
    good, bad = tmp_path / "good.yaml", tmp_path / "bad.yaml"
    good.write_text(yaml.safe_dump(doc()))
    bad.write_text(yaml.safe_dump(doc(runz=1)))
    assert main(["validate", str(good)]) == 0
    assert main(["validate", str(tmp_path)]) == 1
    assert main(["run", str(bad)]) == 2
    assert "did you mean 'runs'" in capsys.readouterr().err
