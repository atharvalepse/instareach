#!/usr/bin/env python3
"""CSV import tests — run: python3 -m unittest discover -s backend/tests"""

import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))                      # backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))     # repo root

from csv_import import rows_from_csv  # noqa: E402
from db import connect  # noqa: E402
from ingest import ingest_leads  # noqa: E402


class TestCSVImport(unittest.TestCase):
    def test_plain_username_header(self):
        rows = rows_from_csv("username,full_name\n@priya,Priya\nmaya,Maya\n")
        self.assertEqual([r["username"] for r in rows], ["priya", "maya"])
        self.assertEqual(rows[0]["full_name"], "Priya")

    def test_extracts_from_instagram_url_column(self):
        rows = rows_from_csv("instagram,name\nhttps://www.instagram.com/john_doe/,John\n")
        self.assertEqual(rows[0]["username"], "john_doe")

    def test_sniffs_handle_when_no_named_column(self):
        rows = rows_from_csv("name,link\nJane,https://instagram.com/jane.smith\n")
        self.assertEqual(rows[0]["username"], "jane.smith")

    def test_extra_columns_preserved(self):
        rows = rows_from_csv("username,company,city\n@a,Acme,NYC\n")
        self.assertEqual(rows[0]["company"], "Acme")
        self.assertEqual(rows[0]["city"], "NYC")

    def test_igscrapper_shape_roundtrips_through_ingest(self):
        csv = ("username,full_name,is_business,category,top_hashtags\n"
               "@fitpriya,Priya Sharma,yes,Fitness Trainer,#fit #flow\n")
        rows = rows_from_csv(csv)
        c = connect(":memory:")
        c.execute("INSERT INTO campaigns (id, name) VALUES ('c1','A')")
        c.commit()
        s = ingest_leads(c, "c1", rows)
        self.assertEqual(s.inserted, 1)
        row = c.execute("SELECT username, enrichment_json FROM contacts").fetchone()
        self.assertEqual(row["username"], "fitpriya")
        self.assertIn("Fitness Trainer", row["enrichment_json"])  # enrichment preserved

    def test_missing_username_row_is_caught_by_ingest(self):
        rows = rows_from_csv("name,note\nNobody,no handle here\n")
        self.assertEqual(rows[0]["username"], "")
        c = connect(":memory:")
        c.execute("INSERT INTO campaigns (id, name) VALUES ('c1','A')")
        c.commit()
        s = ingest_leads(c, "c1", rows)
        self.assertEqual(s.inserted, 0)
        self.assertEqual(len(s.errors), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
