from __future__ import annotations

import pytest

from internship_agent.resume import (
    candidate_name_from_resume,
    fallback_draft,
    read_resume,
    resume_highlights,
)


class TestReadResume:
    def test_reads_txt_file(self, tmp_path):
        path = tmp_path / "resume.txt"
        path.write_text("Jane Doe", encoding="utf-8")
        assert read_resume(path) == "Jane Doe"

    def test_reads_md_file(self, tmp_path):
        path = tmp_path / "resume.md"
        path.write_text("# Jane Doe", encoding="utf-8")
        assert read_resume(path) == "# Jane Doe"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_resume(tmp_path / "nope.txt")

    def test_unsupported_extension_raises(self, tmp_path):
        path = tmp_path / "resume.docx"
        path.write_text("x", encoding="utf-8")
        with pytest.raises(RuntimeError):
            read_resume(path)


class TestCandidateNameFromResume:
    def test_derives_name_from_filename(self, tmp_path):
        path = tmp_path / "jane_doe_resume.pdf"
        assert candidate_name_from_resume("irrelevant text", path) == "Jane Doe"

    def test_falls_back_to_first_short_line_when_no_file(self):
        text = "Jane Doe\n\nEducation\nB.S. Computer Science"
        assert candidate_name_from_resume(text) == "Jane Doe"

    def test_skips_known_section_headers(self):
        text = "Education\nJane Doe\nSkills, Languages & Interests"
        assert candidate_name_from_resume(text) == "Jane Doe"

    def test_default_when_nothing_matches(self):
        text = "this line has way more than five words in it so it is skipped"
        assert candidate_name_from_resume(text) == "Candidate"

    def test_does_not_hardcode_a_specific_persons_name(self):
        # regression check: the original fallback literally returned "Karan"
        # for every resume it couldn't parse, which is wrong for anyone else.
        assert candidate_name_from_resume("") != "Karan"


class TestResumeHighlights:
    def test_finds_known_terms(self):
        text = "Experienced in Python, machine learning, and SQL."
        highlights = resume_highlights(text, max_items=5)
        assert "python" in highlights
        assert "SQL" in highlights
        assert "machine learning" in highlights

    def test_respects_max_items(self):
        text = "python react typescript javascript sql cloud"
        assert len(resume_highlights(text, max_items=2)) == 2

    def test_default_when_nothing_matches(self):
        assert resume_highlights("no relevant keywords here") == [
            "software engineering",
            "AI",
            "data-driven problem solving",
        ]


class TestFallbackDraft:
    def test_produces_subject_and_body(self, tmp_path):
        resume_file = tmp_path / "jane_doe_resume.txt"
        resume_file.write_text("Jane Doe\nPython, machine learning")
        contact = {"company": "Acme", "role": "AI Intern", "contact_name": "Jo Lee"}
        draft = fallback_draft(contact, resume_file.read_text(), resume_file)
        assert "Acme" in draft["subject"]
        assert draft["body"].startswith("Hi Jo,")
        assert "Jane Doe" in draft["body"]

    def test_uses_generic_greeting_without_contact_name(self, tmp_path):
        resume_file = tmp_path / "resume.txt"
        resume_file.write_text("Someone")
        contact = {"company": "Acme", "role": "AI Intern"}
        draft = fallback_draft(contact, resume_file.read_text(), resume_file)
        assert draft["body"].startswith("Hi Hiring Team,")
