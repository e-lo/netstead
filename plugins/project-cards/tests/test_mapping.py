"""The GMNS <-> Wrangler mapping is data (mappings/gmns_to_wrangler.yaml), read once per path."""

import re

import pytest
from netstead_project_cards.mapping import DEFAULT_MAPPING, load_mapping


@pytest.fixture(scope="module")
def m():
    return load_mapping()


def test_the_default_file_ships_inside_the_package():
    assert DEFAULT_MAPPING.is_file() and DEFAULT_MAPPING.parent.name == "mappings"


def test_selectors(m):
    assert m.selector("link") == ("link_id", "model_link_id")
    assert m.selector("node") == ("node_id", "model_node_id")


def test_field_names_round_trip(m):
    assert m.to_card("link", "from_node_id") == "A"
    assert m.to_card("link", "facility_type") == "roadway"
    assert m.to_card("link", "free_speed") == "free_speed"  # unlisted: keeps its GMNS name
    assert m.from_card("link", "B") == "to_node_id"
    assert m.from_card("node", "X") == "x_coord"
    assert m.from_card("link", "free_speed") == "free_speed"


def test_values_pass_through_unless_listed(m):
    assert m.card_value("roadway", "residential") == "residential"
    assert m.gmns_value("roadway", "residential") == "residential"


def test_derived_access_flags_come_from_allowed_uses(m):
    assert m.derive({"allowed_uses": "auto,truck,walk"}) == {"walk_access": 1, "bike_access": 0, "drive_access": 1}
    assert m.derive({}) == {}  # no source column: nothing invented (schema validation reports what is missing)


def test_a_custom_mapping_file_is_read(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("version: 1\nselectors: {link: {link_id: my_id}}\nfields: {}\nvalues: {}\nderived: {}\n")
    assert load_mapping(path).selector("link") == ("link_id", "my_id")


def test_an_unknown_mapping_version_is_refused(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("version: 2\n")
    with pytest.raises(ValueError, match="unsupported mapping version"):
        load_mapping(path)


def test_the_mapping_is_read_only(m):
    with pytest.raises(TypeError):
        m.fields["link"]["lanes"] = "LANES"
    with pytest.raises(TypeError):
        m.selectors["zone"] = {"zone_id": "model_zone_id"}
    with pytest.raises(TypeError):
        m.derived["walk_access"]["column"] = "other"


def test_one_file_is_read_once_however_its_path_is_spelled(tmp_path, monkeypatch):
    path = tmp_path / "m.yaml"
    path.write_text("version: 1\nselectors: {link: {link_id: my_id}}\n")
    monkeypatch.chdir(tmp_path)
    assert load_mapping(path) is load_mapping(str(path)) is load_mapping("m.yaml")
    assert load_mapping() is load_mapping(DEFAULT_MAPPING)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("version: 1\nselectors: [link]\n", "selectors"),
        ("version: 1\nselectors: {link: {link_id: a, id: b}}\n", "selectors.link"),
        ("version: 1\nfields: {link: [a]}\n", "fields.link"),
        ("version: 1\nvalues: {roadway: [a]}\n", "values.roadway"),
        ("version: 1\nderived: {walk_access: {any_of: [walk]}}\n", "derived.walk_access: needs a column"),
        ("version: 1\nderived: {walk_access: {column: allowed_uses, any_of: walk}}\n", "derived.walk_access: any_of"),
        ("- 1\n", "a mapping"),
    ],
)
def test_a_malformed_mapping_file_is_refused(tmp_path, text, problem):
    path = tmp_path / "m.yaml"
    path.write_text(text)
    with pytest.raises(ValueError, match=re.escape(problem)):
        load_mapping(path)


@pytest.mark.parametrize("value", [[1, 2], {"default": 2, "timeofday": []}])
def test_an_unhashable_value_passes_through(m, value):
    assert m.card_value("lanes", value) is value
    assert m.gmns_value("lanes", value) is value
