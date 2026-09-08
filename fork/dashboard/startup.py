"""Local Electron startup endpoints backed by the same lifecycle API as cu."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from fork.cu import branch
from fork.cu.session import DesktopSession, StrictModel
from fork.repl_client import ReplError


class CreateDesktop(StrictModel):
    name: str = Field(pattern=r"^[a-z0-9-]{1,32}$")
    source: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    proxy: bool = False
    desktop_session: DesktopSession | None = None


class ForkDesktop(StrictModel):
    n: int = Field(default=2, ge=1, le=99)
    source: str | None = None
    names: list[str] | None = None
    desktop_session: DesktopSession | None = None


def router() -> APIRouter:
    api = APIRouter()

    @api.post("/api/branches", status_code=201)
    def create(payload: CreateDesktop):
        try:
            return branch.create(**payload.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (RuntimeError, OSError, ReplError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @api.post("/api/branches/{name}/fork", status_code=201)
    def fork(name: str, payload: ForkDesktop):
        try:
            return branch.fork(name, **payload.model_dump())
        except branch.ForkError as exc:
            raise HTTPException(
                409, {"error": str(exc), "branches": exc.branches, "errors": exc.errors}
            ) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (RuntimeError, OSError, ReplError) as exc:
            raise HTTPException(409, str(exc)) from exc

    return api
