"""Step 2 done-when: counts match, all references resolve, every trap present."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

from noodle import dataset as ds
from noodle import schema

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / ds.DEFAULT_DATASET_PATH
FIXTURE_PATH = ROOT / ds.DEFAULT_FIXTURE_PATH


def _load(path: Path) -> dict:
    return ds.load_dataset(path)


def _import_generator():
    spec = importlib.util.spec_from_file_location(
        "build_synthetic_dataset", ROOT / "data" / "build_synthetic_dataset.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def real_dataset() -> dict:
    return _load(DATASET_PATH)


# --------------------------------------------------------------------------- #
# The shipped dataset
# --------------------------------------------------------------------------- #


def test_dataset_file_exists():
    assert DATASET_PATH.is_file(), f"missing {DATASET_PATH}"


def test_real_dataset_is_valid(real_dataset):
    report = ds.validate_dataset(real_dataset)
    assert report.record_count > 0
    assert report.relationship_count > 0


def test_every_required_trap_present(real_dataset):
    report = ds.validate_dataset(real_dataset)
    assert set(report.trap_ids) == set(ds.REQUIRED_TRAP_IDS)
    assert report.trap_count == len(ds.REQUIRED_TRAP_IDS)


def test_section_counts_within_range(real_dataset):
    report = ds.validate_dataset(real_dataset)
    for section, (low, high) in ds.COUNT_RANGES.items():
        assert low <= report.counts[section] <= high, section
    for section, minimum in ds.MIN_COUNTS.items():
        assert report.counts.get(section, 0) >= minimum, section


def test_declared_record_counts_match(real_dataset):
    actual = {name: len(records) for name, records in real_dataset["sections"].items()}
    assert real_dataset["metadata"]["record_counts"] == actual


def test_dataset_is_reproducible(real_dataset):
    """The committed file equals a fresh deterministic build."""
    generated = _import_generator().build()
    assert generated == real_dataset


def test_ids_unique_and_prefixed(real_dataset):
    seen: set[str] = set()
    for section, records in real_dataset["sections"].items():
        for record in records:
            rid = record["id"]
            assert rid not in seen, rid
            seen.add(rid)
            assert schema.ID_PREFIX_TO_SECTION[rid.split(":", 1)[0]] == section


# --------------------------------------------------------------------------- #
# The hand fixture
# --------------------------------------------------------------------------- #


def test_hand_fixture_valid():
    report = ds.validate_dataset(
        _load(FIXTURE_PATH),
        require_metadata=True,
        require_counts=False,
        require_traps=False,
    )
    assert report.record_count == 10


def test_hand_fixture_has_exactly_ten_nodes():
    fixture = _load(FIXTURE_PATH)
    total = sum(len(records) for records in fixture["sections"].values())
    assert total == 10


# --------------------------------------------------------------------------- #
# Validation catches planted faults
# --------------------------------------------------------------------------- #


def _expect_problems(mutate) -> list[str]:
    data = _load(DATASET_PATH)
    mutate(data)
    with pytest.raises(ds.DatasetError) as excinfo:
        ds.validate_dataset(data)
    return excinfo.value.problems


def _expect_problem(mutate) -> str:
    return str(ds.DatasetError(_expect_problems(mutate)))


def test_unresolved_reference_is_reported():
    def mutate(data):
        data["sections"]["tickets"][0]["assignee"] = "person:nobody"

    assert "person:nobody" in _expect_problem(mutate)


def test_duplicate_id_is_reported():
    def mutate(data):
        data["sections"]["people"].append(copy.deepcopy(data["sections"]["people"][0]))

    assert "duplicate id" in _expect_problem(mutate)


def test_missing_trap_is_reported():
    def mutate(data):
        data["traps"] = [
            trap for trap in data["traps"] if trap["id"] != "post-sync-records"
        ]

    assert "required trap 'post-sync-records'" in _expect_problem(mutate)


def test_trap_entity_must_resolve():
    def mutate(data):
        data["traps"][0]["entities"].append("person:does-not-exist")

    assert "person:does-not-exist" in _expect_problem(mutate)


def test_invalid_clearance_is_reported():
    def mutate(data):
        data["sections"]["documents"][0]["clearance"] = 9

    assert "clearance" in _expect_problem(mutate)


def test_confidential_clearance_requires_owner():
    def mutate(data):
        data["sections"]["documents"][0]["clearance"] = 2

    assert "access_owner_id" in _expect_problem(mutate)


def test_unknown_field_is_reported():
    def mutate(data):
        data["sections"]["teams"][0]["typo_field"] = "x"

    assert "unknown field 'typo_field'" in _expect_problem(mutate)


def test_unknown_relationship_type_is_reported():
    def mutate(data):
        data["relationships"]["NOT_A_REL"] = [
            {"src": "person:ada-wren", "dst": "project:auth-migration", "origin": "x"}
        ]

    assert "NOT_A_REL" in _expect_problem(mutate)


def test_relationship_missing_origin_is_reported():
    def mutate(data):
        data["relationships"]["DEPENDS_ON"][0].pop("origin", None)

    assert "missing origin" in _expect_problem(mutate)


def test_relationship_endpoint_label_is_checked():
    def mutate(data):
        data["relationships"]["DEPENDS_ON"].append(
            {"src": "project:auth-migration", "dst": "person:priya-nair", "origin": "x"}
        )

    assert "not allowed for DEPENDS_ON" in _expect_problem(mutate)


def test_history_entity_must_resolve():
    def mutate(data):
        data["sections"]["history"][0]["entity_id"] = "ticket:NOPE-1"
        data["sections"]["history"][0]["entity_type"] = "ticket"

    assert "does not resolve" in _expect_problem(mutate)


def test_metadata_must_be_synthetic():
    def mutate(data):
        data["metadata"]["synthetic"] = False

    assert "synthetic" in _expect_problem(mutate)


def test_metadata_record_counts_must_match():
    def mutate(data):
        data["metadata"]["record_counts"]["people"] = 0

    assert "record_counts" in _expect_problem(mutate)


def test_counts_out_of_range_is_reported():
    def mutate(data):
        # Keep references valid: drop the people too, so only the count trips.
        data["sections"]["teams"] = data["sections"]["teams"][:2]

    problems = _expect_problems(mutate)
    assert any(p.startswith("section 'teams'") for p in problems)


def test_unknown_section_is_reported():
    def mutate(data):
        data["sections"]["widgets"] = []

    assert "unknown section 'widgets'" in _expect_problem(mutate)


def test_missing_sections_object_raises():
    with pytest.raises(ds.DatasetError):
        ds.validate_dataset({"metadata": {}})
