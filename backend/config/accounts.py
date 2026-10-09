import json

from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.http import require_GET, require_POST


@require_GET
def session(request):
    return JsonResponse({"csrfToken": get_token(request),
                         "username": request.user.username if request.user.is_authenticated else None})


@require_POST
def sign_in(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"detail": "无效 JSON。"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"detail": "无效请求。"}, status=400)
    if not isinstance(data.get("username"), str) or not isinstance(data.get("password"), str):
        return JsonResponse({"detail": "请输入用户名和密码。"}, status=400)
    user = authenticate(request, username=data.get("username"), password=data.get("password"))
    if user is None:
        return JsonResponse({"detail": "用户名或密码错误。"}, status=400)
    login(request, user)
    return JsonResponse({"username": user.username, "csrfToken": get_token(request)})


@require_POST
def sign_out(request):
    logout(request)
    return JsonResponse({"status": "ok"})
