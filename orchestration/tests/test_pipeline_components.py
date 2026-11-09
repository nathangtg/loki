from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.audio.stt.speculative import compute_spectral_entropy
from src.graph.grammars import (
    ACTION_GRAMMAR,
    _constrain_index_grammar,
    build_skill_route_grammar_text,
    extract_element_indices,
)
from src.graph.nodes.act import _resolve_locator
from src.graph.nodes.observe import _format_locator_elements
from src.graph.nodes.reason import _assemble_prompt
from src.graph.nodes.reflect import _apply_plan_adjustment
from src.graph.nodes.route import _format_step_context
from src.graph.prompt_utils import (
    compute_lexical_role_scores,
    narrow_elements_iterative,
    rank_elements,
)
from src.graph.vector_store import _DOC_VEC_CACHE, embed_documents
from src.memory import SessionMemory
from src.skills import all_skills, ensure_builtin_skills_loaded, find_skill_by_name
from src.skills.sdef_parser import (
    fetch_sdef_xml,
    find_app_bundle_path,
    parse_sdef_commands,
    parse_sdef_properties,
)
from src.skills.sdef_skill_builder import build_skill_from_sdef


class TestPipelineComponents(unittest.TestCase):
    def setUp(self) -> None:
        ensure_builtin_skills_loaded()

    def test_builtin_skills_registered(self) -> None:
        skills = all_skills()
        names = {s.name for s in skills}
        expected = {
            "mail",
            "spotify",
            "calendar",
            "notes",
            "reminders",
            "finder",
            "file_organizer",
            "shortcuts",
            "documents",
            "contacts",
            "messages",
            "music",
            "meetings",
            "system_control",
        }
        self.assertTrue(expected.issubset(names), f"Missing skills: {expected - names}")
        total_intents = sum(len(s.intents) for s in skills)
        self.assertGreaterEqual(total_intents, 50)

    def test_sdef_command_and_property_discovery(self) -> None:
        finder_bundle = find_app_bundle_path("Finder")
        self.assertIsNotNone(finder_bundle)
        xml_text = fetch_sdef_xml("Finder")
        self.assertIsNotNone(xml_text)
        assert xml_text is not None
        cmds = parse_sdef_commands(xml_text)
        props = parse_sdef_properties(xml_text)
        self.assertGreater(len(cmds), 0)
        self.assertGreater(len(props), 0)

        skill = build_skill_from_sdef("Finder", use_cache=False)
        self.assertIsNotNone(skill)
        assert skill is not None
        self.assertGreaterEqual(len(skill.intents), len(cmds) + len(props))

    def test_dynamic_index_and_skill_enum_gbnf_grammars(self) -> None:
        elements_str = '[00] <button> "Save"\n[03] <textfield> "Search"'
        indices = extract_element_indices(elements_str)
        self.assertIn("[00]", indices)
        self.assertIn("[3]", indices)
        self.assertIn("[03]", indices)

        constrained = _constrain_index_grammar(ACTION_GRAMMAR, indices)
        self.assertIn("idx-token", constrained)
        self.assertIn('\\"[03]\\"', constrained)

        skills = all_skills()[:3]
        route_grammar = build_skill_route_grammar_text(skills)
        self.assertIn("skill-enum", route_grammar)
        self.assertIn("intent-enum", route_grammar)
        for s in skills:
            self.assertIn(f'\\"{s.name}\\"', route_grammar)

    def test_zero_gpu_lexical_role_fast_gate(self) -> None:
        elements = [f"[{i:02d}] <button> Item {i}" for i in range(60)]
        elements[42] = "[42] <button @FinancePanel> Open Fees Summary"
        scores, has_exact = compute_lexical_role_scores(
            elements, "Click Open Fees Summary", "Check Fees Summary"
        )
        self.assertTrue(has_exact)
        self.assertEqual(int(np.argmax(scores)), 42)

        top = rank_elements(
            elements, "Click Open Fees Summary", "Check Fees Summary", top_k=5, use_fast_gate=True
        )
        self.assertTrue(any("[42]" in el for el in top))

        top_iter = narrow_elements_iterative(
            elements, "Click Open Fees Summary", "Check Fees Summary", top_k=5, use_fast_gate=True
        )
        self.assertTrue(any("[42]" in el for el in top_iter))

    def test_hierarchical_container_duplicate_disambiguation(self) -> None:
        locators = [
            {
                "locator": "AXApplication[App]/AXGroup[Drafts]/AXButton[Save]",
                "role": "AXButton",
                "label": "Save",
                "container": "Drafts",
                "interactable": True,
            },
            {
                "locator": "AXApplication[App]/AXGroup[Settings]/AXButton[Save]",
                "role": "AXButton",
                "label": "Save",
                "container": "Settings",
                "interactable": True,
            },
        ]
        fmt_str, loc_map, _ = _format_locator_elements(locators)
        self.assertIn("<button @Drafts> Save", fmt_str)
        self.assertIn("<button @Settings> Save", fmt_str)
        self.assertEqual(
            _resolve_locator("[01]", loc_map),
            "AXApplication[App]/AXGroup[Settings]/AXButton[Save]",
        )

    def test_prefix_stable_prompt_engineering(self) -> None:
        state = {
            "goal": "Open Notes and summarize Q3 report",
            "current_url": "",
            "findings": [{"type": "read", "query_or_url": "q3.pdf", "content": "Revenue grew 14%."}],
            "shortcuts_raw": "New Note -> Cmd+N",
        }
        p1 = _assemble_prompt(state, "Open Notes", True, "  (none)", '[00] button "New"', set())
        p2 = _assemble_prompt(
            state, "Click New", True, "  1. open_app Notes -> OK", '[00] button "New"', set()
        )
        common = 0
        for c1, c2 in zip(p1, p2):
            if c1 == c2:
                common += 1
            else:
                break
        self.assertGreater(common / max(len(p1), 1), 0.80)

    def test_reflect_plan_mutation_and_cross_step_context(self) -> None:
        plan = ["Open App", "Click Missing Button", "Finish"]
        new_plan, new_step, is_done = _apply_plan_adjustment(
            plan, 1, {"adjust": "insert", "new_steps": ["Press Cmd+N"]}
        )
        self.assertFalse(is_done)
        self.assertEqual(new_plan[2], "Press Cmd+N")
        self.assertEqual(new_step, 2)

        ctx = _format_step_context(
            {
                "findings": [{"type": "pdf", "query_or_url": "doc.pdf", "content": "Summary text"}],
                "history": [{"action": "get_contact_email", "result": "Bob: bob@example.com"}],
            }
        )
        self.assertIn("Summary text", ctx)
        self.assertIn("bob@example.com", ctx)

    def test_incremental_embedding_cache_and_session_memory(self) -> None:
        keys = [f"__unit_test_vec_{i}__" for i in range(10)]
        for k in keys:
            _DOC_VEC_CACHE[k] = np.ones(768, dtype=np.float32)
        mat = embed_documents(keys, use_cache=True)
        self.assertEqual(mat.shape, (10, 768))
        for k in keys:
            _DOC_VEC_CACHE.pop(k, None)

        mem = SessionMemory()
        mem.record_action("graph", "open_app", "Notes", "Opened Notes")
        self.assertEqual(mem.current_app, "Notes")

    def test_pdfkit_and_clipboard_skills_live(self) -> None:
        import Quartz
        from Foundation import NSURL

        doc_skill = find_skill_by_name("documents")
        sys_skill = find_skill_by_name("system_control")
        self.assertIsNotNone(doc_skill)
        self.assertIsNotNone(sys_skill)
        assert doc_skill is not None and sys_skill is not None

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "unit_test.pdf"
            pdf_doc = Quartz.PDFDocument.alloc().init()
            pdf_doc.insertPage_atIndex_(Quartz.PDFPage.alloc().init(), 0)
            pdf_doc.writeToURL_(NSURL.fileURLWithPath_(str(pdf_path)))

            meta_out = doc_skill.execute("pdf_metadata", {"path": str(pdf_path)})
            self.assertIn("Pages: 1", meta_out)

        sys_skill.execute("set_clipboard", {"text": "LOKI_UNIT_TEST_CLIP"})
        clip_out = sys_skill.execute("get_clipboard", {})
        self.assertIn("LOKI_UNIT_TEST_CLIP", clip_out)

    def test_spectral_entropy_bounds(self) -> None:
        silence = np.zeros(1600, dtype=np.float32)
        self.assertEqual(compute_spectral_entropy(silence), 0.0)
        t = np.linspace(0, 0.1, 1600, endpoint=False, dtype=np.float32)
        sine = 0.5 * np.sin(2 * np.pi * 440.0 * t)
        rng = np.random.default_rng(42)
        noise = rng.uniform(-0.5, 0.5, size=1600).astype(np.float32)
        self.assertLess(compute_spectral_entropy(sine), compute_spectral_entropy(noise))


if __name__ == "__main__":
    unittest.main()
