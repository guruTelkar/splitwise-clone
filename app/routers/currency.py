from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_pro
from ..models import User
from ..service import USD_RATES, convert_cents

router = APIRouter(prefix="/currency", tags=["currency"])


@router.get("/rates")
def list_rates(user: User = Depends(get_current_user)):
    return {"base": "USD", "rates": USD_RATES}


@router.api_route("/convert", methods=["GET", "POST"])
def convert(
    amount: float,
    from_currency: str,
    to_currency: str,
    _: User = Depends(require_pro),
):
    """Convert a decimal amount between currencies (Pro feature)."""
    converted = convert_cents(round(amount * 100), from_currency.upper(), to_currency.upper())
    return {
        "amount": round(converted / 100, 2),
        "amount_cents": converted,
        "from_currency": from_currency.upper(),
        "to_currency": to_currency.upper(),
    }
