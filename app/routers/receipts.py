import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image

from ..config import settings
from ..deps import require_pro

router = APIRouter(prefix="/receipts", tags=["receipts"])

ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}

# Optional OCR engine; gracefully degrades when tesseract isn't installed.
try:
    import pytesseract  # type: ignore

    _OCR_AVAILABLE = True
except ImportError:  # pragma: no cover
    pytesseract = None  # type: ignore
    _OCR_AVAILABLE = False


def _ocr_text(path: str) -> str:
    if not _OCR_AVAILABLE:
        return ""
    try:
        img = Image.open(path)
        return (pytesseract.image_to_string(img) or "").strip()
    except Exception:
        return ""


@router.post("")
async def upload_receipt(
    file: UploadFile = File(...),
    user=Depends(require_pro),
):
    ext = ("." + file.filename.split(".")[-1].lower()) if "." in file.filename else ".jpg"
    if ext not in ALLOWED:
        raise HTTPException(status_code=400, detail=f"Unsupported file type {ext}")
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    name = f"{user.id}_{stamp}_{uuid.uuid4().hex[:8]}{ext}"
    path = settings.upload_dir / name
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    path.write_bytes(content)

    text = _ocr_text(str(path))
    return {
        "url": f"/uploads/{name}",
        "ocr_text": text,
        "ocr_available": _OCR_AVAILABLE,
        "filename": file.filename,
    }
