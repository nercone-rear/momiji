from .models import Role, Request, Response

async def finalize_request(request: Request, strict: bool = False):
    ...

async def finalize_response(response: Response, strict: bool = False, role: Role = Role.ORIGIN):
    ...
