"""
tests/test_query_processor.py

Tests for app/services/query_processor.py — intent signal extraction
and query expansion. No LLM calls, no mocking needed.
"""

import pytest
from app.services.query_processor import extract_intent, expand_query, IntentSignals, ACRONYM_MAP


class TestExtractIntentSortHints:
    def test_upcoming_gives_deadline_sort(self):
        assert extract_intent("upcoming medical exams").sort_hint == "deadline"

    def test_next_gives_deadline_sort(self):
        assert extract_intent("next engineering exam in India").sort_hint == "deadline"

    def test_cheapest_gives_cost_sort(self):
        assert extract_intent("cheapest language tests").sort_hint == "cost_asc"

    def test_affordable_gives_cost_sort(self):
        assert extract_intent("affordable MBA exams globally").sort_hint == "cost_asc"

    def test_free_gives_cost_sort_and_flag(self):
        r = extract_intent("free engineering exams Asia")
        assert r.sort_hint == "cost_asc"
        assert r.free_hint is True

    def test_hardest_gives_difficulty_sort(self):
        assert extract_intent("hardest exams in the world").sort_hint == "difficulty"

    def test_toughest_gives_difficulty_sort(self):
        assert extract_intent("toughest entrance test India").sort_hint == "difficulty"

    def test_default_is_relevance(self):
        assert extract_intent("GRE exam prep").sort_hint == "relevance"


class TestExtractIntentFreeHint:
    def test_no_fee(self):
        assert extract_intent("no fee government exams India").free_hint is True

    def test_zero_cost(self):
        assert extract_intent("zero cost certification tests").free_hint is True

    def test_without_fee(self):
        assert extract_intent("law exams without fee USA").free_hint is True

    def test_normal_query_not_free(self):
        assert extract_intent("medical entrance exams").free_hint is False


class TestExtractIntentYearHint:
    def test_year_2025_extracted(self):
        assert extract_intent("medical entrance exams India 2025").year_hint == 2025

    def test_year_2026_extracted(self):
        assert extract_intent("upcoming exams 2026 Asia").year_hint == 2026

    def test_no_year_gives_none(self):
        assert extract_intent("IELTS preparation tips").year_hint is None

    def test_old_year_not_extracted(self):
        # Only 2024-2032 should be extracted
        assert extract_intent("history of exams in 2010").year_hint is None


class TestExtractIntentCountryHints:
    def test_india_detected(self):
        assert "India" in extract_intent("medical exams in India").country_hints

    def test_usa_detected(self):
        assert "Usa" in extract_intent("law school exams usa").country_hints or \
               "USA" in extract_intent("law school exams USA").country_hints

    def test_multiple_countries(self):
        hints = extract_intent("exams in india and uk").country_hints
        lower = [h.lower() for h in hints]
        assert "india" in lower and "uk" in lower

    def test_no_country_in_generic_query(self):
        assert extract_intent("graduate admissions test").country_hints == []


class TestExtractIntentCategoryHint:
    def test_medical_category(self):
        assert extract_intent("MBBS entrance exam").category_hint == "Medical Admissions"

    def test_mba_category(self):
        assert extract_intent("MBA admissions tests globally").category_hint == "Business School"

    def test_engineering_category(self):
        assert extract_intent("engineering entrance exams").category_hint == "Engineering Admissions"

    def test_law_category(self):
        assert extract_intent("law school admission test USA").category_hint == "Law School"

    def test_language_category(self):
        assert extract_intent("English proficiency IELTS").category_hint == "Language Proficiency"

    def test_graduate_category(self):
        assert extract_intent("GRE graduate programs").category_hint == "Graduate Admissions"

    def test_finance_category(self):
        assert extract_intent("CFA exam finance").category_hint == "Finance Certification"

    def test_civil_service_category(self):
        assert extract_intent("UPSC civil services exam").category_hint == "Government"

    def test_no_hint_for_generic(self):
        assert extract_intent("competitive examination").category_hint is None


class TestExtractIntentAcronyms:
    def test_gre_detected(self):
        assert "GRE" in extract_intent("GRE exam preparation").acronyms_found

    def test_ielts_detected(self):
        assert "IELTS" in extract_intent("IELTS band 7").acronyms_found

    def test_neet_detected(self):
        assert "NEET" in extract_intent("NEET medical India").acronyms_found

    def test_multiple_acronyms(self):
        found = extract_intent("GRE and GMAT for MBA").acronyms_found
        assert "GRE" in found and "GMAT" in found

    def test_no_false_positives(self):
        found = extract_intent("medical entrance exam India").acronyms_found
        assert "INDIA" not in found  # not an acronym

    def test_all_acronyms_in_map(self):
        for acr in ["GRE","GMAT","SAT","LSAT","MCAT","IELTS","TOEFL","UPSC","GATE","JEE","NEET","CFA","BAR"]:
            assert acr.lower() in ACRONYM_MAP, f"{acr} missing from ACRONYM_MAP"


class TestExpandQuery:
    def test_noise_words_stripped(self):
        expanded = expand_query("best GRE exam preparation guide")
        assert "best" not in expanded.lower()

    def test_top_stripped(self):
        expanded = expand_query("top engineering exams in India")
        assert "top" not in expanded.lower()

    def test_acronym_expanded(self):
        expanded = expand_query("GRE preparation")
        assert "graduate" in expanded.lower()

    def test_ielts_expanded(self):
        expanded = expand_query("IELTS band score")
        assert "language" in expanded.lower()

    def test_neet_expanded(self):
        expanded = expand_query("NEET exam India")
        assert "medical" in expanded.lower()

    def test_category_hint_appended(self):
        signals = extract_intent("MBBS entrance exam")
        expanded = expand_query("MBBS entrance exam", signals)
        assert "medical" in expanded.lower()

    def test_original_query_preserved(self):
        expanded = expand_query("GRE exam")
        assert "gre" in expanded.lower()

    def test_empty_expansion_safe(self):
        expanded = expand_query("exam")
        assert len(expanded) > 0

    def test_signals_can_be_passed(self):
        from app.services.query_processor import IntentSignals
        signals = IntentSignals(sort_hint="deadline", free_hint=True, category_hint="Medical Admissions")
        expanded = expand_query("medical exams India", signals)
        assert isinstance(expanded, str) and len(expanded) > 0


class TestIntentSignalsToDict:
    def test_to_dict_has_all_keys(self):
        sig = extract_intent("GRE exam 2025 India")
        d = sig.to_dict()
        assert all(k in d for k in ("sort_hint","free_hint","year_hint","country_hints",
                                    "category_hint","acronyms_found"))

    def test_to_dict_values_serialisable(self):
        import json
        sig = extract_intent("free NEET medical exam India 2025")
        d = sig.to_dict()
        # Should not raise
        json.dumps(d)


class TestEdgeCases:
    def test_very_short_query(self):
        sig = extract_intent("GRE")
        assert sig.acronyms_found == ["GRE"]

    def test_all_caps_not_acronym(self):
        sig = extract_intent("HELLO WORLD exam")
        assert "HELLO" not in sig.acronyms_found

    def test_no_crash_on_empty(self):
        sig = extract_intent("")
        assert sig.sort_hint == "relevance"

    def test_free_hint_overrides_deadline(self):
        # free + upcoming → free_hint should be set, sort should be cost_asc
        sig = extract_intent("free upcoming exams")
        assert sig.free_hint is True
        assert sig.sort_hint == "cost_asc"
