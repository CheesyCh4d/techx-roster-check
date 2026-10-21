"""Loading packs: the built-in pack, mistakes people make editing one, user copies."""

from __future__ import annotations

import pytest

from techx_roster_check.packs import available_packs, find_pack, load_pack
from techx_roster_check.rules import RULE_TYPES, PackError


def test_builtin_health_pack_loads(health):
    assert health.name == "District data health"
    assert len(health.rules) >= 20
    assert {type(rule).type_name for rule in health.rules} == set(RULE_TYPES) - {"range"}


@pytest.mark.parametrize("text,message", [
    ('[[rules]]\nid = "a"\ntype = "presnt"\ncolumn = "x"', 'type "presnt", which is not one of'),
    ('[[rules]]\nid = "a"\ntype = "present"\ncolum = "x"', "needs a \"column\""),
    ('[[rules]]\nid = "a"\ntype = "present"\ncolumn = "x"\nlable = "X"', "does not use: lable"),
    ('[[rules]]\ntype = "present"\ncolumn = "x"', 'Rule #1 needs a "id"'),
    ('[[rules]]\nid = "a"\ntype = "format"\ncolumn = "x"\npattern = "(["', "not a valid regular expression"),
    ('[[rules]]\nid = "a"\ntype = "present"\ncolumn = "x"\nseverity = "high"', '"error" or "warning"'),
    ('[[rules]]\nid = "a"\ntype = "range"\ncolumn = "x"\nmin = "3"', "should be a number"),
    ('[[rules]]\nid = "a"\ntype = "compare"\nleft = "x"\nright = "y"\nop = "=>"\nkind = "date"', '"op" that should be one of'),
    ('[[rules]]\nid = "a"\ntype = "present"\ncolumn = "x"\nwhen = { column = "y" }', 'needs one of "in"'),
    ('[[rules]]\nid = "a"\ntype = "reference"\ncolumn = "x"\nfile = "nope.csv"', "was not found"),
    ('[[rules]]\nid = "a"\ntype = "present"\ncolumn = "x"\n[[rules]]\nid = "a"\ntype = "present"\ncolumn = "y"', "two rules with the id"),
    ('[columns]\ndob = ["birth"]\ngrade = ["birth"]', "can only mean one column"),
    ('[pack]\nnmae = "x"', "does not use: nmae"),
    ('[[rule]]\nid = "a"', "sections this tool does not use: rule"),
    ('[[rules]\nid = "a"', "not valid TOML"),
])
def test_mistakes_are_explained(write_pack, text, message):
    with pytest.raises(PackError) as excinfo:
        write_pack(text)
    assert message in str(excinfo.value)


def test_disabled_rules_are_left_out(write_pack):
    pack = write_pack('[[rules]]\nid = "a"\ntype = "present"\ncolumn = "x"\nenabled = false\n')
    assert pack.rules == [] and pack.disabled == 1


def test_default_label(write_pack):
    pack = write_pack('[[rules]]\nid = "a"\ntype = "present"\ncolumns = ["x", "y"]\n')
    assert pack.rules[0].label == "x and y are filled in"


def test_user_copy_replaces_builtin(user_packs):
    (user_packs / "health.toml").write_text('[pack]\nname = "Ours"\n')
    assert available_packs()["health"] == user_packs / "health.toml"
    assert load_pack(find_pack("health")).name == "Ours"


def test_web_cannot_name_a_path(tmp_path):
    path = tmp_path / "x.toml"
    path.write_text("")
    assert find_pack(str(path)) == path
    with pytest.raises(PackError):
        find_pack(str(path), allow_paths=False)
