# routers/restaurants.py
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from database import get_session
from models import Restaurant

router = APIRouter(prefix="/restaurants", tags=["restaurants"])


class RestaurantIn(BaseModel):
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    address: Optional[str] = None
    phone: Optional[str] = None


@router.post("/")
def create_restaurant(body: RestaurantIn, session: Session = Depends(get_session)):
    restaurant = Restaurant(**body.model_dump())
    session.add(restaurant)
    session.commit()
    session.refresh(restaurant)
    return restaurant