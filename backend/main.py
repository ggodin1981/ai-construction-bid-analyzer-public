import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import cv2
import fitz
import numpy as np
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-nano")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "")
OCR_SPACE_ENDPOINT = os.getenv("OCR_SPACE_ENDPOINT", "https://api.ocr.space/parse/image")
OCR_SPACE_LANGUAGE = os.getenv("OCR_SPACE_LANGUAGE", "eng")
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="Construction Bid Review API", version="1.0.0")

default_local_origins = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
]
origins = ["*"] if FRONTEND_ORIGIN == "*" else list(dict.fromkeys([FRONTEND_ORIGIN, *default_local_origins]))
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str
    document_text: str


def classify_document_type(text: str, filename: str) -> str:
    source = f"{filename.lower()}\n{text.lower()[:6000]}"
    categories = [
        ("Change order", ["change order", "change request", "cco", "pco"]),
        ("RFI / clarification", ["request for information", "rfi", "clarification"]),
        ("Specification package", ["section ", "specification", "submittal", "division 0", "division 1"]),
        ("Drawing / plan set", ["sheet", "elevation", "floor plan", "detail", "legend"]),
        ("Bid invitation / scope package", ["bid package", "invitation to bid", "proposal", "scope of work"]),
    ]
    for label, indicators in categories:
        if any(indicator in source for indicator in indicators):
            return label
    return "Construction document package"


def render_pdf_page_images(path: str, scale: float = 1.5) -> List[np.ndarray]:
    page_images: List[np.ndarray] = []
    with fitz.open(path) as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image_bytes = pix.tobytes("png")
            np_buffer = np.frombuffer(image_bytes, dtype=np.uint8)
            image = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
            if image is not None:
                page_images.append(image)
    return page_images


def estimate_skew_angle(binary_image: np.ndarray) -> float:
    coordinates = np.column_stack(np.where(binary_image > 0))
    if coordinates.shape[0] < 50:
        return 0.0

    angle = cv2.minAreaRect(coordinates)[-1]
    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90
    return round(float(angle), 2)


def analyze_page_image(image: np.ndarray) -> Dict[str, float]:
    grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(grayscale, None, 12, 7, 21)
    edges = cv2.Canny(denoised, 50, 150)
    binary_inv = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        15,
    )

    edge_density = float(np.count_nonzero(edges)) / float(edges.size)
    ink_ratio = float(np.count_nonzero(binary_inv)) / float(binary_inv.size)
    skew_angle = estimate_skew_angle(binary_inv)

    return {
        "edgeDensity": round(edge_density, 4),
        "inkRatio": round(ink_ratio, 4),
        "skewAngle": skew_angle,
    }


def classify_visual_profile(page_metrics: List[Dict[str, float]]) -> str:
    if not page_metrics:
        return "Not evaluated"

    average_edge_density = sum(metric["edgeDensity"] for metric in page_metrics) / len(page_metrics)
    average_ink_ratio = sum(metric["inkRatio"] for metric in page_metrics) / len(page_metrics)

    if average_edge_density > 0.08 and average_ink_ratio < 0.12:
        return "Drawing-heavy candidate"
    if average_ink_ratio > 0.12:
        return "Text-dense scanned document"
    return "Mixed document"


def preprocess_image_for_ocr(image: np.ndarray) -> Tuple[np.ndarray, Dict[str, float]]:
    grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if grayscale.shape[1] < 1400:
        scale_ratio = 1400.0 / float(grayscale.shape[1])
        grayscale = cv2.resize(
            grayscale,
            None,
            fx=scale_ratio,
            fy=scale_ratio,
            interpolation=cv2.INTER_CUBIC,
        )

    denoised = cv2.fastNlMeansDenoising(grayscale, None, 14, 7, 21)
    normalized = cv2.normalize(denoised, None, 0, 255, cv2.NORM_MINMAX)
    thresholded = cv2.adaptiveThreshold(
        normalized,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        15,
    )
    sharpened = cv2.GaussianBlur(thresholded, (0, 0), 2.2)
    processed = cv2.addWeighted(thresholded, 1.45, sharpened, -0.45, 0)

    diagnostics = analyze_page_image(cv2.cvtColor(processed, cv2.COLOR_GRAY2BGR))
    return processed, diagnostics


def call_ocr_space_file(
    file_tuple: Tuple[str, bytes, str],
    extra_fields: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    api_key = OCR_SPACE_API_KEY.strip()
    if not api_key:
        return None

    payload = {
        "language": OCR_SPACE_LANGUAGE,
        "isOverlayRequired": "false",
        "detectOrientation": "true",
        "scale": "true",
        "OCREngine": "2",
    }
    if extra_fields:
        payload.update(extra_fields)

    response = requests.post(
        OCR_SPACE_ENDPOINT,
        headers={"apikey": api_key},
        data=payload,
        files={"file": file_tuple},
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("IsErroredOnProcessing"):
        error_message = data.get("ErrorMessage") or data.get("ErrorDetails") or "OCR processing failed"
        raise RuntimeError(str(error_message))

    parsed_results = data.get("ParsedResults") or []
    parsed_text_parts = [
        (result.get("ParsedText") or "").strip()
        for result in parsed_results
        if isinstance(result, dict)
    ]
    parsed_text = "\n".join(part for part in parsed_text_parts if part).strip()

    return {
        "text": parsed_text,
        "page_count": len(parsed_results) or None,
        "ocr_exit_code": data.get("OCRExitCode"),
        "engine": "OCR.Space Engine 2",
    }


def call_ocr_space(path: str, filename: str) -> Optional[Dict[str, Any]]:
    with open(path, "rb") as file_handle:
        return call_ocr_space_file(
            (filename, file_handle.read(), "application/pdf"),
            {"filetype": "PDF"},
        )


def perform_cv_ocr_fallback(path: str, filename: str) -> Dict[str, Any]:
    page_images = render_pdf_page_images(path)
    if not page_images:
        raise RuntimeError("No renderable pages were available for CV preprocessing.")

    ocr_text_parts: List[str] = []
    page_metrics: List[Dict[str, float]] = []

    for index, page_image in enumerate(page_images, start=1):
        processed_image, diagnostics = preprocess_image_for_ocr(page_image)
        page_metrics.append(diagnostics)
        ok, encoded = cv2.imencode(".png", processed_image)
        if not ok:
            raise RuntimeError(f"OpenCV could not encode page {index} for OCR.")

        ocr_result = call_ocr_space_file(
            (f"{os.path.splitext(filename)[0]}-page-{index}.png", encoded.tobytes(), "image/png"),
            {"filetype": "PNG"},
        )
        page_text = (ocr_result or {}).get("text", "").strip()
        if page_text:
            ocr_text_parts.append(page_text)

    return {
        "text": "\n".join(ocr_text_parts).strip(),
        "page_count": len(page_images),
        "engine": "OCR.Space Engine 2 with OpenCV preprocessing",
        "page_metrics": page_metrics,
        "visual_classification": classify_visual_profile(page_metrics),
    }


def extract_pdf_contents(path: str, filename: str) -> Tuple[str, Dict[str, Any]]:
    try:
        text_parts: List[str] = []
        page_lengths: List[int] = []
        with fitz.open(path) as doc:
            for page in doc:
                page_text = page.get_text("text").strip()
                text_parts.append(page_text)
                page_lengths.append(len(page_text))

        text = "\n".join(part for part in text_parts if part).strip()
        page_count = len(page_lengths)
        blank_pages = sum(1 for length in page_lengths if length == 0)
        low_text_pages = sum(1 for length in page_lengths if 0 < length < 120)
        average_chars = round(sum(page_lengths) / page_count) if page_count else 0
        ocr_attempted = False
        ocr_used = False
        ocr_status = "Not attempted"
        ocr_engine = "Not used"
        cv_preprocessing_applied = False
        cv_pipeline_status = "Not attempted"
        cv_visual_classification = "Not evaluated"
        cv_average_skew_angle = 0.0
        cv_average_edge_density = 0.0
        cv_average_ink_ratio = 0.0
        page_metrics: List[Dict[str, float]] = []

        pipeline_observations: List[str] = []
        if blank_pages:
            pipeline_observations.append(
                "One or more pages appear image-only; OCR and layout analysis would be the next production step."
            )
        if low_text_pages:
            pipeline_observations.append(
                "Some pages have low text density, which usually indicates scanned sheets, title blocks, or drawing-heavy pages."
            )
        if not pipeline_observations:
            pipeline_observations.append(
                "Selectable text was extracted cleanly enough for a first-pass document analytics review."
            )

        if not text and OCR_SPACE_API_KEY.strip():
            ocr_attempted = True
            try:
                ocr_result = perform_cv_ocr_fallback(path, filename)
                ocr_text = (ocr_result or {}).get("text", "").strip()
                if ocr_text:
                    text = ocr_text
                    ocr_used = True
                    ocr_status = "OCR fallback succeeded"
                    ocr_engine = (ocr_result or {}).get("engine", "OCR.Space")
                    cv_preprocessing_applied = True
                    cv_pipeline_status = "OpenCV preprocessing + OCR fallback succeeded"
                    if (ocr_result or {}).get("page_count"):
                        page_count = int((ocr_result or {})["page_count"])
                    average_chars = round(len(text) / page_count) if page_count else len(text)
                    low_text_pages = 0
                    blank_pages = 0
                    page_metrics = (ocr_result or {}).get("page_metrics", [])
                    cv_visual_classification = (ocr_result or {}).get("visual_classification", "Not evaluated")
                    if page_metrics:
                        cv_average_skew_angle = round(
                            sum(abs(metric["skewAngle"]) for metric in page_metrics) / len(page_metrics),
                            2,
                        )
                        cv_average_edge_density = round(
                            sum(metric["edgeDensity"] for metric in page_metrics) / len(page_metrics),
                            4,
                        )
                        cv_average_ink_ratio = round(
                            sum(metric["inkRatio"] for metric in page_metrics) / len(page_metrics),
                            4,
                        )
                    pipeline_observations.append(
                        "Selectable text was unavailable, so the pipeline rendered page images, applied OpenCV preprocessing, and fell back to OCR for text recovery."
                    )
                    if cv_visual_classification != "Not evaluated":
                        pipeline_observations.append(
                            f"OpenCV classified the visual profile as {cv_visual_classification.lower()} before OCR."
                        )
                else:
                    ocr_status = "OCR fallback returned no text"
                    cv_preprocessing_applied = True
                    cv_pipeline_status = "OpenCV preprocessing ran, but OCR returned no text"
            except Exception as exc:
                ocr_status = f"OCR fallback failed: {exc}"
                cv_preprocessing_applied = True
                cv_pipeline_status = f"OpenCV/OCR fallback failed: {exc}"
                pipeline_observations.append(
                    "OCR fallback was attempted but did not complete successfully."
                )
        elif not text:
            ocr_status = "OCR fallback not configured"
            cv_pipeline_status = "OpenCV preprocessing not configured because OCR fallback is disabled"

        profile = {
            "filename": filename,
            "pageCount": page_count,
            "extractedCharacters": len(text),
            "averageCharsPerPage": average_chars,
            "blankPages": blank_pages,
            "lowTextPages": low_text_pages,
            "detectedDocumentType": classify_document_type(text, filename),
            "extractionMode": "OCR fallback via OCR.Space" if ocr_used else "Selectable PDF text via PyMuPDF",
            "ocrRecommendation": (
                "OCR is already being used for scanned or image-only packages in this environment."
                if ocr_used
                else (
                    "Recommended before production use on drawing-heavy or scanned packages."
                    if blank_pages or low_text_pages
                    else "Not immediately required for this file, but still valuable for mixed drawing sets."
                )
            ),
            "ocrAttempted": ocr_attempted,
            "ocrUsed": ocr_used,
            "ocrStatus": ocr_status,
            "ocrEngine": ocr_engine,
            "cvPreprocessingApplied": cv_preprocessing_applied,
            "cvPipelineStatus": cv_pipeline_status,
            "cvVisualClassification": cv_visual_classification,
            "cvAverageSkewAngle": cv_average_skew_angle,
            "cvAverageEdgeDensity": cv_average_edge_density,
            "cvAverageInkRatio": cv_average_ink_ratio,
            "pipelineObservations": pipeline_observations,
        }

        return text, profile
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to read PDF: {exc}")


def fallback_analysis(text: str, filename: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    preview = text[:5000]
    risk_items = []
    lower = text.lower()
    if "deadline" in lower or "due" in lower:
        risk_items.append("Submission deadline may require review.")
    if "exclusion" in lower or "excluded" in lower:
        risk_items.append("Exclusions are mentioned and should be validated before bidding.")
    if "change order" in lower:
        risk_items.append("Change order language exists; pricing impact should be reviewed.")
    if not risk_items:
        risk_items = ["Manual review recommended for scope, exclusions, and bid assumptions."]

    return {
        "summary": "The package was processed, but the live review model was unavailable. The extracted text indicates a construction bid or scope document that still requires commercial review before pricing is finalized.",
        "bidInfo": {
            "projectName": filename.replace(".pdf", ""),
            "tradeScope": "Specialty contractor scope / construction bid package",
            "location": "Not clearly detected",
            "deadline": "Not clearly detected",
            "materials": "Review document for listed materials",
            "exclusions": "Review exclusions section if available",
        },
        "riskItems": risk_items,
        "reviewFlags": [
            "Deadline and submission instructions require manual confirmation.",
            "Scope assignment and exclusions should be validated before release.",
            "Document may not reflect all addenda, scanned notes, or external attachments.",
        ],
        "recommendedActions": [
            "Confirm bid deadline, addenda status, and submission requirements.",
            "Validate exclusions and owner-supplied scope before final pricing.",
            "Escalate incomplete scope language for estimator and project management review.",
        ],
        "requiredClarifications": [
            "Confirm whether all current addenda and bulletins are included in the package.",
            "Clarify ownership of excluded or deferred scope items before proposal issue.",
            "Confirm any existing-condition assumptions that could affect labor or material exposure.",
        ],
        "bidRecommendation": "Escalate",
        "recommendationRationale": "The package can be reviewed, but the fallback path cannot support a confident bid decision without live AI analysis and human review of scope, exclusions, and schedule language.",
        "commercialQualifications": [
            "Proposal is subject to final review of scope documents and addenda.",
            "Pricing should exclude items not clearly assigned within the bid package.",
            "Existing conditions and site constraints remain subject to verification.",
        ],
        "estimatorReviewMemo": "Escalate for estimator review before pricing release. The fallback path is sufficient for triage, but a trusted bid decision still requires confirmation of scope ownership, exclusions, and current document status.",
        "sourceEvidence": [
            "Analysis based only on extracted selectable PDF text.",
            "Live model unavailable; output may omit nuance normally captured in AI review.",
        ],
        "reviewBasis": "Fallback review generated from extracted PDF text only. Scanned sheets, attachments, and non-selectable markups may not be represented.",
        "readinessScore": 72,
        "readinessLabel": "Needs Review",
        "documentText": preview,
        "aiProvider": "Automated review engine",
        "documentProfile": profile,
        "analysisGeneratedAt": datetime.now(timezone.utc).isoformat(),
    }


def call_openrouter(messages: List[Dict[str, str]], response_format_json: bool = False) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    payload: Dict[str, Any] = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": 0.2,
    }
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ai-construction-bid-analyzer.demo",
            "X-Title": "AI Construction Bid Analyzer",
        },
        json=payload,
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def analyze_with_ai(text: str, filename: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    prompt = f"""
You are a senior preconstruction analyst supporting commercial specialty trade subcontractors.
Analyze this construction bid document and return only valid JSON with this exact shape:
{{
  "summary": "2-3 sentence executive summary written like a real bid review note",
  "bidInfo": {{
    "projectName": "detected project name or Not clearly detected",
    "tradeScope": "detected trade/scope, concise but specific",
    "location": "detected location or Not clearly detected",
    "deadline": "detected due date/deadline or Not clearly detected",
    "materials": "important materials mentioned or Not clearly detected",
    "exclusions": "exclusions/assumptions mentioned or Not clearly detected"
  }},
  "riskItems": [
    "3-5 prioritized risk statements written as commercial concerns, not generic bullets"
  ],
  "reviewFlags": [
    "3-6 short dashboard-style flags such as addenda risk, incomplete design, missing schedule detail, or unclear scope assignment"
  ],
  "recommendedActions": [
    "3-5 concrete next steps an estimator or PM should take before bid submission"
  ],
  "requiredClarifications": [
    "3-5 specific clarifications or RFIs that should be resolved before the proposal is finalized"
  ],
  "bidRecommendation": "Bid or No Bid or Escalate",
  "recommendationRationale": "1-2 sentence explanation of why this recommendation is appropriate based only on the document",
  "commercialQualifications": [
    "3-5 proposal qualifications or clarifications that should likely be carried into a real bid response"
  ],
  "estimatorReviewMemo": "short internal note written as if for a preconstruction manager or estimator handoff",
  "sourceEvidence": [
    "3-6 short evidence snippets copied or closely paraphrased from the document"
  ],
  "reviewBasis": "one sentence explaining scope of analysis and any limitations",
  "readinessScore": 0,
  "readinessLabel": "Ready or Needs Review or High Risk"
}}

Rules:
- Focus on bid readiness, margin risk, missing details, exclusions, deadlines, schedule pressure, drawing completeness, field verification exposure, and scope clarity.
- Do not invent information. Use "Not clearly detected" when missing.
- readinessScore should be 0 to 100.
- `bidRecommendation` must be one of `Bid`, `No Bid`, or `Escalate`.
- Score conservatively. Use this rough rubric:
  - 80-100: scope is clear, low ambiguity, few commercial blockers.
  - 55-79: workable but requires clarification or review before release.
  - 0-54: material uncertainty, missing information, or meaningful execution/commercial risk.
- If drawings are incomplete, field verification is required, exclusions are significant, or timing is tight, the score should usually drop materially.
- Risk items must be specific and credible. Avoid filler like "review carefully" unless tied to a document fact.
- `reviewFlags` should be short, scannable operational warnings suitable for dashboard display.
- Recommended actions must be operational, such as clarifying exclusions, validating quantities, confirming addenda, or pricing schedule impacts.
- `requiredClarifications` should read like real pre-bid RFIs or internal clarification points.
- `bidRecommendation` guidance:
  - `Bid` only when the package appears commercially workable with manageable qualifiers.
  - `Escalate` when the opportunity may still be viable but needs leadership/estimator review because of uncertainty, schedule pressure, or scope gaps.
  - `No Bid` only when the document clearly suggests an unmanageable commercial position or severe lack of scope definition.
- `commercialQualifications` should sound like real proposal language and stay grounded in document facts.
- `estimatorReviewMemo` should read like a credible internal note, not marketing copy.
- sourceEvidence should improve trust. Keep each item short.
- Return strict JSON only.

Filename: {filename}
Document profile:
{json.dumps(profile, indent=2)}

Document text:
{text[:14000]}
"""
    raw = call_openrouter(
        [
            {"role": "system", "content": "You extract construction bid intelligence from documents. Return strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        response_format_json=True,
    )
    result = json.loads(raw)
    result["documentText"] = text[:14000]
    result["aiProvider"] = OPENROUTER_MODEL
    result["documentProfile"] = profile
    result["analysisGeneratedAt"] = datetime.now(timezone.utc).isoformat()
    return result


def ask_with_ai(question: str, document_text: str) -> Dict[str, Any]:
    prompt = f"""
You are a senior preconstruction analyst answering a question about a construction bid package.
Return only valid JSON with this exact shape:
{{
  "answer": "direct answer in 2-4 sentences",
  "confidence": "High or Medium or Low",
  "evidence": [
    "2-4 short evidence points quoted or closely paraphrased from the document"
  ],
  "limitations": "one sentence stating any uncertainty or missing detail"
}}

Rules:
- Answer based only on the provided document text.
- Do not invent facts, quantities, dates, or scope.
- If the answer is not clearly supported by the document, say so directly.
- `confidence` must reflect document support, not model confidence.
- Keep `evidence` short and specific.
- Return strict JSON only.

Document:
{document_text[:14000]}

Question:
{question}
"""
    raw = call_openrouter(
        [
            {
                "role": "system",
                "content": "You answer construction bid questions conservatively and return strict JSON only."
            },
            {"role": "user", "content": prompt},
        ],
        response_format_json=True,
    )
    return json.loads(raw)


@app.get("/")
def health() -> Dict[str, str]:
    return {"status": "running", "service": "AI Construction Bid Analyzer API"}


@app.post("/api/analyze")
async def analyze_document(file: UploadFile = File(...)) -> Dict[str, Any]:
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    safe_filename = file.filename.replace("/", "_").replace("\\", "_")
    path = os.path.join(UPLOAD_DIR, safe_filename)
    with open(path, "wb") as f:
        f.write(await file.read())

    text, profile = extract_pdf_contents(path, safe_filename)
    if not text:
        raise HTTPException(status_code=400, detail="No selectable text found. OCR can be added for scanned plans later.")

    try:
        return analyze_with_ai(text, safe_filename, profile)
    except Exception:
        return fallback_analysis(text, safe_filename, profile)


@app.post("/api/ask")
async def ask_question(payload: AskRequest) -> Dict[str, Any]:
    if not payload.document_text.strip():
        raise HTTPException(status_code=400, detail="Document text is required.")
    try:
        return ask_with_ai(payload.question, payload.document_text)
    except Exception:
        return {
            "answer": "A live document answer could not be generated at this time.",
            "confidence": "Low",
            "evidence": [
                "Live AI question answering is currently unavailable.",
                "Retry after confirming the model endpoint is reachable."
            ],
            "limitations": "This response is a service fallback, not a document-grounded analyst answer."
        }
