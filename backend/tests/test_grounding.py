"""Deterministic geometry/evidence/worker tests; real model runs use ground_page.py."""

import hashlib
from pathlib import Path

import pymupdf
import pytest
from pydantic import ValidationError

from app.grounding.document import build_registries, box_in_pixels, pixels_to_pdf_box, render_page, registry_fingerprint
from app.grounding.schema import Element, GroundingItem, TargetProposal, TargetRequest, TargetSelections
from app.grounding.selection import validate_selection
from app.models import Mention, Segment, SpokenWord
from app.pipeline.pdf_extract import extract_pdf, add_geometry
from app.vlm.provider import GPUWorkerProvider, VLMFailure
from app.vlm.schema import VLMConfig


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch):
    monkeypatch.setattr("app.pipeline.pdf_extract._load_ocr", lambda: None)


def make_slide(path, rotation=0, cropped=False):
    with pymupdf.open() as doc:
        page = doc.new_page(width=800, height=500)
        page.insert_text((110, 100), "Learning rate", fontsize=25)
        page.insert_text((110, 170), "theta = theta - alpha * gradient", fontsize=18)
        page.insert_text((110, 230), "alpha controls learning", fontsize=18)
        page.draw_line((490, 380), (710, 380))
        page.draw_line((490, 380), (490, 150))
        page.draw_bezier((510, 180), (560, 370), (640, 370), (690, 210))
        if cropped:
            page.set_cropbox(pymupdf.Rect(70, 30, 760, 460))
        page.set_rotation(rotation)
        doc.save(path)
    return path


def make_request(tmp_path):
    path = make_slide(tmp_path / "slide.pdf")
    registry = build_registries(path)[0]
    original, marked, viewport = render_page(path, registry, tmp_path / "renders")
    alpha = next(e for e in registry.elements if e.text == "alpha" and "p1_l1" in e.parent_ids)
    block = next(e for e in registry.elements if e.id in alpha.parent_ids and e.kind == "block")
    item = GroundingItem(id="k1", page=1, content="alpha controls the update size",
                         source_quote="alpha controls the update size", start=0, end=4,
                         segment_ids=[0], block_ids=[block.id], target_kind="word")
    request = TargetRequest(image_path=marked, registry=registry, viewport=viewport,
                            items=[item], segments=[Segment(start=0,end=4,text=item.source_quote)])
    return path, request, alpha


def proposal(request, target, **updates):
    data = dict(knowledge_id="k1", page=1, status="selected", target_element_id=target.id,
                target_text=target.text, confidence=0.9)
    data.update(updates)
    return TargetSelections(selections=[TargetProposal(**data)])


def test_registry_reuses_ids_and_preserves_formula_minus(tmp_path):
    path = make_slide(tmp_path / "slide.pdf")
    native = extract_pdf(path)
    add_geometry(path, native)
    registry = build_registries(path)[0]
    assert {w.word_id for w in native.pages[0].words} <= {e.id for e in registry.elements}
    formula = next(e for e in registry.elements if e.kind == "formula")
    assert formula.text == "theta = theta - alpha * gradient"
    assert any(e.text == "-" and e.kind == "symbol" for e in registry.elements)
    assert registry == build_registries(path)[0]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [(0, 0, 1, 1), (0.08, 0.1, 0.94, 0.9)])
def test_rotation_crop_scale_roundtrip_and_original_unchanged(tmp_path, rotation, crop):
    path = make_slide(tmp_path / "rotated.pdf", rotation=rotation, cropped=True)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    registry = build_registries(path)[0]
    original, marked, viewport = render_page(path, registry, tmp_path / "out", zoom=1.37, crop=crop)
    assert registry.width == 690 and registry.height == 430
    assert pymupdf.Pixmap(str(original)).width == viewport.width
    assert pymupdf.Pixmap(str(marked)).height == viewport.height
    e = next(e for e in registry.elements if e.kind == "word" and e.id in viewport.visible_element_ids)
    image_box = box_in_pixels(e.bbox, viewport)
    assert 0 <= image_box[0] < image_box[2] <= viewport.width + 0.01
    assert 0 <= image_box[1] < image_box[3] <= viewport.height + 0.01
    expected = (e.bbox[0]*690, e.bbox[1]*430, e.bbox[2]*690, e.bbox[3]*430)
    assert pixels_to_pdf_box(image_box, viewport, registry) == pytest.approx(expected, abs=0.001)
    with pymupdf.open(path) as doc:
        matches = doc[0].search_for(e.text)
        assert any(pymupdf.Rect(expected).intersects(rect) for rect in matches)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("updates,reason", [
    ({"target_element_id":"made_up"},"invalid_element_id"),
    ({"target_element_id":"3"},"invalid_element_id"),
    ({"page":2},"wrong_page"),
    ({"target_text":"beta"},"target_text_mismatch"),
    ({"confidence":0.2},"low_confidence"),
    ({"alternatives":["missing"]},"invalid_alternative_id"),
])
def test_invalid_targets_are_rejected_without_bbox(tmp_path, updates, reason):
    _, request, alpha = make_request(tmp_path)
    row = validate_selection(request, proposal(request, alpha, **updates))["results"][0]
    assert row["reason"] == reason
    assert row["bbox"] is None and row["target_element_id"] is None


def test_precise_target_and_rich_content_adapter(tmp_path):
    _, request, alpha = make_request(tmp_path)
    result = validate_selection(request, proposal(request, alpha))["results"][0]
    assert result["status"] == "accepted"
    assert result["bbox"] == list(alpha.bbox)
    assert result["source_segment_ids"] == [0]
    item = request.items[0]
    assert GroundingItem.from_content_item(item, target_kind="word") == item


def test_formula_symbol_unknown_symbol_and_figure_evidence(tmp_path):
    _, request, alpha = make_request(tmp_path)
    formula = next(e for e in request.registry.elements if e.kind == "formula")
    request.items[0].target_kind = "formula"
    assert validate_selection(request, proposal(request, formula))["results"][0]["status"] == "accepted"
    minus = next(e for e in request.registry.elements if e.kind == "symbol" and e.text == "-")
    request.items[0].target_kind = "symbol"
    row = validate_selection(request, proposal(request, minus))["results"][0]
    assert row["reason"] == "unsupported_target"
    figure = next(e for e in request.registry.elements if e.kind == "figure")
    request.items[0].target_kind = "figure"
    request.items[0].block_ids = []
    assert validate_selection(request, proposal(request, figure))["results"][0]["reason"] == "figure_without_block_evidence"
    request.items[0].block_ids = [figure.id]
    assert validate_selection(request, proposal(request, figure))["results"][0]["status"] == "accepted"


def test_ambiguity_and_timed_transliteration_mentions(tmp_path):
    _, request, alpha = make_request(tmp_path)
    request.items[0].block_ids = []
    assert validate_selection(request, proposal(request, alpha))["results"][0]["reason"] == "ambiguous_repeated_text"
    request.items[0].source_quote = request.segments[0].text = "알파는 학습률입니다"
    request.mentions = [Mention(page=1,word_ids=[alpha.id],time=0,end=3,spoken_text="알파",score=99)]
    assert validate_selection(request, proposal(request, alpha))["results"][0]["reason"] == "unsupported_target"
    request.mentions_registry_hash = registry_fingerprint(request.registry)
    assert validate_selection(request, proposal(request, alpha))["results"][0]["status"] == "accepted"
    other_alpha = next(e for e in request.registry.elements if e.id != alpha.id and e.text == "alpha")
    request.mentions[0].word_ids = [alpha.id,other_alpha.id]
    assert validate_selection(request, proposal(request, alpha))["results"][0]["reason"] == "ambiguous_repeated_text"
    request.mentions[0].word_ids = [alpha.id]
    request.mentions[0].time, request.mentions[0].end = 5, 7
    assert validate_selection(request, proposal(request, alpha))["results"][0]["reason"] == "unsupported_target"
    reply = proposal(request, alpha, status="ambiguous", target_element_id=None, alternatives=[alpha.id])
    assert validate_selection(request, reply)["results"][0]["status"] == "ambiguous"


def test_fabricated_quote_timing_and_invisible_target(tmp_path):
    _, request, alpha = make_request(tmp_path)
    request.items[0].source_quote = "a fabricated statement"
    assert validate_selection(request, proposal(request, alpha))["results"][0]["reason"] == "source_quote_not_found"
    request.items[0].source_quote = request.segments[0].text
    request.items[0].start, request.items[0].end = 0, 1
    assert validate_selection(request, proposal(request, alpha))["results"][0]["reason"] == "unverified_subsegment_time"
    request.segments[0].words = [SpokenWord(start=2,end=3,text=request.items[0].source_quote)]
    assert validate_selection(request, proposal(request, alpha))["results"][0]["reason"] == "quote_outside_word_times"
    request.items[0].end = 4
    request.viewport.visible_element_ids.remove(alpha.id)
    assert validate_selection(request, proposal(request, alpha))["results"][0]["reason"] == "target_outside_viewport"


def test_scanned_pdf_never_invents_words(tmp_path):
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0,0,300,200), False)
    image.clear_with(255)
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as doc:
        page=doc.new_page(width=600,height=400)
        page.insert_image(page.rect, stream=image.tobytes("png"))
        doc.save(path)
    registry = build_registries(path)[0]
    assert not any(e.kind in {"word","symbol","formula"} for e in registry.elements)
    assert any(e.kind == "figure" and e.recognition == "embedded_image" for e in registry.elements)


def test_registry_and_response_schema_reject_bad_coordinates(tmp_path):
    with pytest.raises(ValidationError):
        Element(id="bad",page=1,kind="word",bbox=[0.4,0.3,0.2,1],text="a",source="pdf")
    with pytest.raises(ValidationError):
        TargetProposal(knowledge_id="k",page=1,status="selected",target_element_id="x",confidence=1,bbox=[0,0,1,1])


def test_formula_operator_changes_cannot_pass_legacy_quote_normalization(tmp_path):
    _, request, alpha = make_request(tmp_path)
    request.segments[0].text = "theta = theta - alpha * gradient"
    request.items[0].source_quote = "theta = theta + alpha * gradient"
    row = validate_selection(request,proposal(request,alpha))["results"][0]
    assert row["reason"] == "source_formula_mismatch" and row["bbox"] is None


def test_greek_symbol_case_is_not_silently_changed(tmp_path):
    font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not font.exists():
        pytest.skip("Greek PDF fixture needs DejaVuSans")
    path = tmp_path / "symbols.pdf"
    with pymupdf.open() as doc:
        page=doc.new_page(width=600,height=400)
        page.insert_font(fontname="math",fontfile=str(font))
        page.insert_text((50,100),"θ = Θ - α ∇L",fontname="math",fontsize=25)
        doc.save(path)
    registry=build_registries(path)[0]
    target=next(e for e in registry.elements if e.kind == "symbol" and e.text == "θ")
    _,image,viewport=render_page(path,registry,tmp_path/"symbols-render")
    item=GroundingItem(id="k1",page=1,content="uppercase parameter",source_quote="Θ is the parameter",
                       start=0,end=4,segment_ids=[0],target_kind="symbol")
    request=TargetRequest(image_path=image,registry=registry,viewport=viewport,items=[item],
                          segments=[Segment(start=0,end=4,text=item.source_quote)])
    assert validate_selection(request,proposal(request,target,target_text="Θ"))["results"][0]["reason"] == "target_text_mismatch"
    assert validate_selection(request,proposal(request,target))["results"][0]["reason"] == "unsupported_target"
    operator=next(e for e in registry.elements if e.text == "∇" and e.kind == "symbol")
    assert operator.recognition == "native_pdf_character"
    parent=next(e for e in registry.elements if e.text == "∇L")
    assert parent.id in operator.parent_ids
    assert operator.bbox[2] < parent.bbox[2]
    request.items[0].source_quote = request.segments[0].text = "∇ is the gradient operator"
    assert validate_selection(request,proposal(request,operator))["results"][0]["status"] == "accepted"


def test_invalid_pixel_geometry_and_forged_viewport_are_rejected(tmp_path):
    _, request, _ = make_request(tmp_path)
    with pytest.raises(ValueError,match="outside image"):
        pixels_to_pdf_box((-3,0,100,100),request.viewport,request.registry)
    bad=request.model_dump(mode="json")
    bad["viewport"]["pdf_normalized_to_pixels"]=[0,0,0,0,0,0]
    with pytest.raises(ValidationError,match="singular"):
        TargetRequest.model_validate(bad)
    bad=request.model_dump(mode="json")
    bad["viewport"]["crop"]=[0.8,0.8,1,1]
    with pytest.raises(ValidationError,match="outside viewport crop"):
        TargetRequest.model_validate(bad)


def test_optional_ocr_keeps_estimated_words_separate_from_original(tmp_path,monkeypatch):
    path=tmp_path/"low-text.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=600,height=400).insert_text((300,300),"Native",fontsize=14)
        doc.save(path)
    original=path.read_bytes()
    class OCR:
        def __call__(self,png):
            class Result:
                boxes=[[[20,20],[240,20],[240,60],[20,60]]]
                txts=["학습률 alpha"]
                scores=[0.99]
            return Result()
    monkeypatch.setattr("app.pipeline.pdf_extract._load_ocr",lambda:OCR())
    registry=build_registries(path,ocr=True)[0]
    words=[e for e in registry.elements if e.kind == "word"]
    assert any(e.text == "Native" and e.source == "pdf" for e in words)
    assert any(e.text == "학습률" and e.source == "ocr" and e.recognition == "ocr_estimated_word_box" for e in words)
    assert path.read_bytes() == original


class DeterministicSelector:
    def __init__(self, config):
        self.calls=0

    def select_targets(self, request, reduced=False):
        self.calls += 1
        target = next(e for e in request.registry.elements if e.text == "alpha" and "p1_l1" in e.parent_ids)
        return {"selection": proposal(request,target).model_dump(mode="json"), "metrics":{"mock":True,"calls":self.calls}}

    def close(self):
        pass


def test_provider_selection_cache_alignment_evidence_invalidation(tmp_path):
    _, request, alpha = make_request(tmp_path)
    config=VLMConfig(cache_dir=tmp_path/"cache",lease_path=tmp_path/"gpu.lock",timeout_seconds=15)
    with GPUWorkerProvider(config,runtime_factory=DeterministicSelector) as provider:
        first=provider.select_targets(request)
        assert first["grounding"]["results"][0]["target_element_id"] == alpha.id
        assert provider.select_targets(request)["cache_hit"] is True
        request.alignment_hash="changed"
        assert provider.select_targets(request)["cache_hit"] is False
        request.image_mode="original"
        assert provider.select_targets(request)["cache_hit"] is False
        request.items[0].source_quote="invented"
        with pytest.raises(VLMFailure,match="invalid_speech_evidence"):
            provider.select_targets(request)
