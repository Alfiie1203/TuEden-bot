"""
test_wp_client.py
Pruebas unitarias para WordPressClient en modo simulado (sin WordPress).
"""
import json
import tempfile
from pathlib import Path

from models.post_draft import PostDraft, PostType
from core.wp_client import WordPressClient


def _make_draft(post_type: str = "guia") -> PostDraft:
    return PostDraft(
        post_type        = PostType(post_type),
        title            = "Título de prueba",
        content          = "<p>Contenido de prueba</p>",
        meta_description = "Meta de prueba para SEO.",
        focus_keyword    = "producto prueba",
        affiliate_url    = "https://amazon.es/dp/TEST",
        wp_post_id       = None,
    )


class TestSimulatedWordPressClient:
    def test_create_draft_returns_id(self, tmp_path):
        client = WordPressClient(simulate=True, output_dir=str(tmp_path))
        draft  = _make_draft()
        wp_id  = client.create_draft(draft)
        assert isinstance(wp_id, int)
        assert wp_id > 0

    def test_create_draft_writes_json_file(self, tmp_path):
        client = WordPressClient(simulate=True, output_dir=str(tmp_path))
        draft  = _make_draft("comparativa")
        client.create_draft(draft)

        json_files = list(tmp_path.glob("draft_*.json"))
        assert len(json_files) == 1

        with open(json_files[0], encoding="utf-8") as f:
            data = json.load(f)

        assert data["title"]      == "Título de prueba"
        assert data["status"]     == "draft"
        assert data["post_type"]  == "comparativa"
        assert data["ai_generated"] is True

    def test_create_multiple_drafts_unique_ids(self, tmp_path):
        client = WordPressClient(simulate=True, output_dir=str(tmp_path))
        id1 = client.create_draft(_make_draft("comparativa"))
        id2 = client.create_draft(_make_draft("guia"))
        id3 = client.create_draft(_make_draft("resena_seo"))
        assert len({id1, id2, id3}) == 3  # todos distintos

    def test_test_connection_always_true(self, tmp_path):
        client = WordPressClient(simulate=True, output_dir=str(tmp_path))
        assert client.test_connection() is True

    def test_mode_property(self, tmp_path):
        client = WordPressClient(simulate=True, output_dir=str(tmp_path))
        assert client.mode == "simulado"
