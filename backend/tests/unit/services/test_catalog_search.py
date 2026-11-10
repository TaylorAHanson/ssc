"""Keyword matching shared by the agent's catalog search and the Discover page."""
from app.services import catalog_search as cs


def test_stems_strip_one_suffix_and_stay_a_prefix_of_the_word():
    for word, root in (("deliveries", "deliver"), ("delivery", "deliver"), ("shipments", "shipment"),
                       ("pricing", "pric"), ("sales", "sale"), ("orders", "order"), ("cost", "cost")):
        assert cs.stem(word) == root
        assert word.startswith(root)


def test_queries_drop_stopwords_and_split_on_separators():
    assert cs.tokenize("Show me the late_shipment-delivery data for EMEA") == ["late", "shipment", "delivery", "emea"]
    assert cs.term_groups("late shipments") == [["late"], ["shipment"]]
    assert cs.term_groups("shipment shipments") == [["shipment"]]


def test_a_form_matches_at_the_start_of_any_word():
    haystack = cs.normalized("main.sc.on_time_delivery_daily")
    assert cs.like_pattern("deliver") == "%deliver%"
    assert cs.form_in("deliver", haystack) and not cs.form_in("ime", haystack)


def test_a_word_inside_another_counts_half_and_only_when_long_enough():
    assert cs.form_weight("customer", cs.normalized("dim_customer")) == 1.0
    assert cs.form_weight("customer", cs.normalized("dimcustomer")) == 0.5
    assert cs.form_weight("late", cs.normalized("unrelated")) == 0.0
