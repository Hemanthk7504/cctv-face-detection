from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/login")
def login_page():
    return RedirectResponse("/", 303)


@router.post("/login")
def login():
    return RedirectResponse("/", 303)


@router.get("/logout")
def logout():
    return RedirectResponse("/", 303)
