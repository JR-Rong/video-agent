from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

import requests

from ...utils.files import ensure_dir
from .base import UploadTask


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    open_id: str | None = None
    scope: str | None = None


class DouyinOpenAPIUploader:
    """
    抖音开放平台上传骨架（你后续按官方文档补全 endpoint/字段/签名/分片逻辑）。
    目前不会真正上传成功：会在调用时抛 NotImplementedError，保证“接口留好”。

    典型流程（示意）：
    1) OAuth 获取 access_token (code -> token)
    2) 初始化上传（拿到 upload_url / upload_id）
    3) 上传文件（直传/分片）
    4) 发布视频（带标题、描述、话题等）
    """

    def __init__(self, *, api_cfg: dict[str, Any], token_dir: str) -> None:
        self.api_cfg = api_cfg
        self.base_url = (api_cfg.get("base_url") or "https://open.douyin.com").rstrip("/")
        self.client_id = api_cfg.get("client_id") or ""
        self.client_secret = api_cfg.get("client_secret") or ""
        self.redirect_uri = api_cfg.get("redirect_uri") or ""
        self.scopes = api_cfg.get("scopes") or []
        self.token_path = os.path.join(token_dir, "douyin.json")
        ensure_dir(token_dir)

    # ---------- Token storage ----------
    def load_token(self) -> Optional[OAuthToken]:
        if not os.path.exists(self.token_path):
            return None
        with open(self.token_path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return OAuthToken(**obj)

    def save_token(self, token: OAuthToken) -> None:
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump(token.__dict__, f, ensure_ascii=False, indent=2)

    # ---------- OAuth endpoints (placeholders) ----------
    def build_authorize_url(self, state: str) -> str:
        # 你后续按抖音 OAuth 文档拼接
        raise NotImplementedError("Implement Douyin OAuth authorize url according to official docs.")

    def exchange_code_for_token(self, code: str) -> OAuthToken:
        # 你后续按抖音 OAuth 文档实现
        raise NotImplementedError("Implement code->token exchange according to Douyin OpenAPI.")

    def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        raise NotImplementedError("Implement token refresh according to Douyin OpenAPI.")

    # ---------- Upload flow (placeholders) ----------
    def init_upload(self, *, access_token: str, file_size: int) -> dict[str, Any]:
        raise NotImplementedError("Implement init upload endpoint for Douyin.")

    def upload_file(self, *, upload_url: str, video_path: str) -> dict[str, Any]:
        raise NotImplementedError("Implement actual file upload (possibly multipart) for Douyin.")

    def publish(self, *, access_token: str, upload_result: dict[str, Any], task: UploadTask) -> dict[str, Any]:
        raise NotImplementedError("Implement publish endpoint for Douyin.")

    def upload(self, task: UploadTask) -> dict[str, Any]:
        token = self.load_token()
        if not token or not token.access_token:
            raise RuntimeError(
                "Missing Douyin OAuth token. Implement OAuth flow and save token to "
                f"{self.token_path}"
            )

        file_size = os.path.getsize(task.video_path)
        init = self.init_upload(access_token=token.access_token, file_size=file_size)
        up = self.upload_file(upload_url=init["upload_url"], video_path=task.video_path)
        return self.publish(access_token=token.access_token, upload_result=up, task=task)