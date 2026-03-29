# src/app/endpoints/admin.py
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["Admin"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Serve the admin UI single-page application."""
    return templates.TemplateResponse("admin.html", {"request": request})
