from __future__ import annotations

import unittest

from paper_search.sources.dblp import (
    DBLPSource,
    _base_name,
    _build_dblp_query,
    _is_author_only_query,
    _matches_venue,
    parse_person_xml,
)


class TestBuildDblpQuery(unittest.TestCase):
    def test_author_only_drops_free_text(self):
        self.assertEqual(
            _build_dblp_query("Zhenhua Zhu", "Zhenhua Zhu"),
            "author:Zhenhua_Zhu:",
        )

    def test_author_only_case_insensitive(self):
        self.assertEqual(
            _build_dblp_query("zhenhua zhu", "Zhenhua Zhu"),
            "author:Zhenhua_Zhu:",
        )

    def test_author_with_real_keywords(self):
        self.assertEqual(
            _build_dblp_query("processing-in-memory", "Zhenhua Zhu"),
            "author:Zhenhua_Zhu: processing-in-memory",
        )

    def test_author_with_venue(self):
        self.assertEqual(
            _build_dblp_query("", "Zhenhua Zhu", "HPCA"),
            "author:Zhenhua_Zhu: venue:HPCA",
        )

    def test_keywords_with_venue_no_author(self):
        self.assertEqual(
            _build_dblp_query("sparse tensor core", None, "ISCA"),
            "sparse tensor core venue:ISCA",
        )

    def test_empty_everything(self):
        self.assertEqual(_build_dblp_query("", None), "")


class TestAuthorOnlyQuery(unittest.TestCase):
    def test_empty_query(self):
        self.assertTrue(_is_author_only_query("", "Zhenhua Zhu"))
        self.assertTrue(_is_author_only_query("   ", "Zhenhua Zhu"))

    def test_query_equals_author(self):
        self.assertTrue(_is_author_only_query("Zhenhua Zhu", "Zhenhua Zhu"))

    def test_real_keyword_query(self):
        self.assertFalse(_is_author_only_query("RRAM", "Zhenhua Zhu"))


class TestBaseName(unittest.TestCase):
    def test_strips_disambiguation_suffix(self):
        self.assertEqual(_base_name("Zhenhua Zhu 0002"), "zhenhua zhu")

    def test_plain_name(self):
        self.assertEqual(_base_name("Zhenhua Zhu"), "zhenhua zhu")


class TestParseAuthorsPidMapping(unittest.TestCase):
    def test_pid_goes_to_dblp_pid_not_orcid(self):
        info = {
            "authors": {
                "author": [
                    {"@pid": "07/4259-2", "text": "Zhenhua Zhu 0002"},
                    {"@pid": "w/YuWang2", "text": "Yu Wang 0002"},
                ]
            }
        }
        authors = DBLPSource()._parse_authors(info)
        self.assertEqual(len(authors), 2)
        self.assertEqual(authors[0].name, "Zhenhua Zhu 0002")
        self.assertEqual(authors[0].dblp_pid, "07/4259-2")
        self.assertIsNone(authors[0].orcid)

    def test_single_author_dict(self):
        info = {"authors": {"author": {"@pid": "x/YuanXie", "text": "Yuan Xie 0001"}}}
        authors = DBLPSource()._parse_authors(info)
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0].dblp_pid, "x/YuanXie")


class TestVenueMatch(unittest.TestCase):
    def test_substring_case_insensitive(self):
        from paper_search.models import Paper

        paper = Paper(title="t", authors=[], source="dblp", source_id="k", venue="HPCA")
        self.assertTrue(_matches_venue(paper, "hpca"))
        self.assertTrue(_matches_venue(paper, "HPCA"))
        self.assertFalse(_matches_venue(paper, "ISCA"))
        self.assertTrue(_matches_venue(paper, None))


_PERSON_XML = """<?xml version="1.0"?>
<dblpperson name="Zhenhua Zhu 0002" pid="07/4259-2" n="2">
<r><inproceedings key="conf/hpca/XieZLHL0Y0025" mdate="2026-04-18">
<author pid="348/7364">Tongxin Xie</author>
<author pid="07/4259-2">Zhenhua Zhu 0002</author>
<author pid="w/YuWang2">Yu Wang 0002</author>
<title>UniNDP: A Unified Compilation and Simulation Tool for Near DRAM Processing Architectures.</title>
<pages>624-640</pages>
<year>2025</year>
<booktitle>HPCA</booktitle>
<ee>https://doi.org/10.1109/HPCA61900.2025.00054</ee>
<url>db/conf/hpca/hpca2025.html#XieZLHL0Y0025</url>
</inproceedings></r>
<r><article key="journals/tc/LiYWZLZ26" mdate="2026-04-19">
<author pid="17/8346-1">Yueting Li 0001</author>
<author pid="07/4259-2">Zhenhua Zhu 0002</author>
<title>ReNN-RV: Run-Time PE Reconfiguration for DNN Inference Acceleration With Custom RISC-V ISA.</title>
<year>2026</year>
<journal>IEEE Trans. Computers</journal>
<ee>https://doi.org/10.1109/TC.2026.3669718</ee>
</article></r>
<r><editor key="homepages/07/4259-2">
<author pid="07/4259-2">Zhenhua Zhu 0002</author>
</editor></r>
</dblpperson>
"""


class TestParsePersonXml(unittest.TestCase):
    def test_parses_records(self):
        papers = parse_person_xml(_PERSON_XML)
        self.assertEqual(len(papers), 2)

        hpca = papers[0]
        self.assertEqual(hpca.source_id, "conf/hpca/XieZLHL0Y0025")
        self.assertEqual(hpca.venue, "HPCA")
        self.assertEqual(hpca.year, 2025)
        self.assertEqual(hpca.doi, "10.1109/HPCA61900.2025.00054")
        self.assertEqual(len(hpca.authors), 3)
        self.assertEqual(hpca.authors[1].dblp_pid, "07/4259-2")
        self.assertIsNone(hpca.authors[1].orcid)

        journal = papers[1]
        self.assertEqual(journal.venue, "IEEE Trans. Computers")
        self.assertEqual(journal.year, 2026)

    def test_skips_non_publication_records(self):
        papers = parse_person_xml(_PERSON_XML)
        self.assertNotIn("homepages", [p.source_id for p in papers])


if __name__ == "__main__":
    unittest.main()
