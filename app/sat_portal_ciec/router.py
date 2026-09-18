from fastapi import APIRouter
from .service import health

router = APIRouter(
    prefix="/sat/portal",
    tags=["SAT Portal CIEC"]
)

@router.get("/health")
def connector_health():
    return health()