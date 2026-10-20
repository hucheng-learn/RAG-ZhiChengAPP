from fastapi import Request


def runtime(request: Request):
    result = request.app.state.runtime
    result.require_storage()
    return result
