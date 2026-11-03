"""GET /pay: turns a tappable https link into a upi:// redirect.

The redirect target is rebuilt server-side from validated fields, so this can
never be used as an open redirect to an arbitrary URL.
"""
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from src.loop.upi import upi_link

router = APIRouter()
_VPA = re.compile(r"^[A-Za-z0-9._\-]{2,64}@[A-Za-z0-9.\-]{2,32}$")
_AMOUNT = re.compile(r"^\d{1,9}(\.\d{1,2})?$")


@router.get("/pay")
async def pay(pa: str, am: str, pn: str = "", tn: str = ""):
    if not _VPA.match(pa) or not _AMOUNT.match(am) or len(pn) > 80 or len(tn) > 80:
        raise HTTPException(status_code=400, detail="invalid payment link")
    return RedirectResponse(upi_link(pa, pn, round(float(am) * 100), tn), status_code=302)
