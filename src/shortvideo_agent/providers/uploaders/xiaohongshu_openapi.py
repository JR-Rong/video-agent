from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from ...utils.files import ensure_dir
from .base import UploadTask


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    user_id: str | None = None
    scope: str | None = None


class XiaohongshuOpenAPIUploader:
    def __init__(self, *, api_cfg: dict[str, Any], token_dir: str) -> None:
        self.api_cfg = api_cfg
        self.base_url = (api_cfg.get("base_url") or "").rstrip("/")
        self.client_id = api_cfg.get("client_id") or ""
        self.client_secret = api_cfg.get("client_secret") or ""
        self.redirect_uri = api_cfg.get("redirect_uri") or ""
        self.scopes = api_cfg.get("scopes") or []
        self.token_path = os.path.join(token_dir, "xiaohongshu.json")
        ensure_dir(token_dir)

    def load_token(self) -> Optional[OAuthToken]:
        if not os.path.exists(self.token_path):
            return None
        with open(self.token_path, "r", encoding="utf-8") as f:
            return OAuthToken(**json.load(f))

    def save_token(self, token: OAuthToken) -> None:
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump(token.__dict__, f, ensure_ascii=False, indent=2)

    def build_authorize_url(self, state: str) -> str:
        raise NotImplementedError("Implement Xiaohongshu OAuth authorize url.")

    def exchange_code_for_token(self, code: str) -> OAuthToken:
        raise NotImplementedError("Implement Xiaohongshu code->token exchange.")

    def init_upload(self, *, access_token: str, file_size: int) -> dict[str, Any]:
        raise NotImplementedError("Implement Xiaohongshu init upload.")

    def upload_file(self, *, upload_url: str, video_path: str) -> dict[str, Any]:
        raise NotImplementedError("Implement Xiaohongshu upload file.")

    def publish(self, *, access_token: str, upload_result: dict[str, Any], task: UploadTask) -> dict[str, Any]:
        raise NotImplementedError("Implement Xiaohongshu publish.")

    def upload(self, task: UploadTask) -> dict[str, Any]:
        token = self.load_token()
        if not token or not token.access_token:
            raise RuntimeError(f"Missing Xiaohongshu OAuth token at {self.token_path}")
        file_size = os.path.getsize(task.video_path)
        init = self.init_upload(access_token=token.access_token, file_size=file_size)
        up = self.upload_file(upload_url=init["upload_url"], video_path=task.video_path)
        return self.publish(access_token=token.access_token, upload_result=up, task=task)