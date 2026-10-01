"""No CORS grant: only the paired extension can fetch loopback jobs."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from providers.tab_bridge import authorized, broker

router = APIRouter()


class BrowserResult(BaseModel):
    provider: str = Field(max_length=16)
    id: str = Field(max_length=128)
    owner: str = Field(max_length=128)
    lease: str = Field(max_length=128)
    result: dict


def allowed(request):
    return request.client is not None and authorized(request.client.host, request.headers.get("authorization"))


@router.get("/browser/jobs/{provider}")
def get_job(provider: str, owner: str, request: Request):
    if not allowed(request):
        return JSONResponse(status_code=403, content={"error":"Browser pairing required"})
    try:
        return {"job":broker.claim(provider, owner)}
    except ValueError:
        return JSONResponse(status_code=400, content={"error":"Invalid browser identity"})


@router.post("/browser/result")
def finish_job(value: BrowserResult, request: Request):
    if not allowed(request):
        return JSONResponse(status_code=403, content={"error":"Browser pairing required"})
    try:
        broker.finish(value.provider, value.id, value.owner, value.lease, value.result)
    except ValueError:
        return JSONResponse(status_code=409, content={"error":"Stale browser job"})
    return {"ok":True}
