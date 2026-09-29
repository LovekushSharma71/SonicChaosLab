# SPDX-License-Identifier: Apache-2.0
"""Guardrail tests: scope gate, experiment allowlist, config-set batch, output checks, fallback."""

from __future__ import annotations

from chaoslab.lessons.models import Card, Misconception
from chaoslab.llm import guards


def _card() -> Card:
    return Card(
        objective="learn bgp",
        in_scope_subtopics=["a", "b", "c"],
        key_concepts=["1", "2", "3", "4", "5"],
        command_field_meanings={"show bgp summary": {"meaning": "session dashboard"}},
        healthy_state_expectations="both neighbors Established",
        expected_chaos_effects={"c_shut_one_link": "one neighbor drops"},
        common_misconceptions=[Misconception(misconception="m", why_wrong="w", correct_model="c")]
        * 5,
        out_of_scope=["iBGP"],
        redirect_line="Let's return to the lesson.",
    )


def test_scope_gate():
    keywords = ["bgp", "route", "neighbor"]
    assert guards.scope_gate("what is a bgp neighbor?", keywords)
    assert not guards.scope_gate("recipe for pasta", keywords)


def test_experiment_relevance():
    vocab = ["show bgp*", "show ip route*", "sonic-db-cli APPL_DB *"]
    assert guards.experiment_relevance("show bgp summary", vocab)
    assert guards.experiment_relevance("sonic-db-cli APPL_DB keys x", vocab)
    assert not guards.experiment_relevance("show version", vocab)


def test_experiment_safety_allows_reads():
    assert guards.experiment_safety("show bgp summary")
    assert guards.experiment_safety("ping -c 3 10.0.2.10")
    assert guards.experiment_safety('sonic-db-cli APPL_DB keys "ROUTE_TABLE:*"')
    assert guards.experiment_safety('vtysh -c "show ip route"')


def test_experiment_safety_denies_mutations():
    assert not guards.experiment_safety("sudo config interface shutdown Ethernet0")
    assert not guards.experiment_safety("systemctl stop bgp")
    assert not guards.experiment_safety('sonic-db-cli CONFIG_DB hset "X" y z')
    assert not guards.experiment_safety("sonic-clear fdb all")


def test_config_set_batch_one_bad_rejects():
    ok, bad = guards.config_set_validate(
        ["config vlan add 200", "config vlan member add 200 Ethernet4"]
    )
    assert ok and bad is None
    ok, bad = guards.config_set_validate(["config vlan add 200", "docker stop bgp"])
    assert not ok and bad == "docker stop bgp"
    ok, bad = guards.config_set_validate(["config reload -y"])
    assert not ok and bad == "config reload -y"
    ok, bad = guards.config_set_validate(["config vlan add 1 ; rm -rf /"])
    assert not ok


def test_output_checks():
    assert guards.within_length("One sentence. Two sentences.")
    assert not guards.within_length("")
    assert guards.mentions_fact("Ethernet0 went down", ["iface:Ethernet0:oper: up → down"])
    assert not guards.mentions_fact("nothing relevant here", ["route:10.0.2.0/24:next_hops: a → b"])
    ok, _ = guards.check_scripted("Ethernet0 is down now.", ["iface:Ethernet0:oper: up → down"])
    assert ok
    bad, reason = guards.check_scripted("", [])
    assert not bad and reason


def test_tiered_fallback_is_labelled():
    card = _card()
    baseline = guards.tiered_fallback(card, "explain_baseline")
    assert baseline.startswith(guards.FALLBACK_PREFIX)
    impact = guards.tiered_fallback(card, "explain_impact", "c_shut_one_link")
    assert "one neighbor drops" in impact
