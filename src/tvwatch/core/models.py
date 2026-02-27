from typing import Any, Dict, List, Optional
import re
from pydantic import BaseModel, Field, HttpUrl, field_validator
from .config import CONFIG

_ALLOWED = re.compile(CONFIG.ALLOWED_URL_RE)


def _validate_allowed(url: str) -> str:
    if not _ALLOWED.match(url or ""):
        raise ValueError("URL not allowed by policy")
    return url


class Episode(BaseModel):
    url: HttpUrl
    name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    position: Optional[int] = None

    @field_validator("url", mode="before")
    @classmethod
    def _check_allowed(cls, v: str):
        return _validate_allowed(v)

    def model_dump_public(self) -> Dict[str, Any]:
        """
        JSON shape used by existing outputs (CLI/Discord).
        """
        d = {"url": str(self.url), "name": self.name}
        d.update({k: v for k, v in self.metadata.items() if k not in d})
        if self.position is not None:
            d["position"] = self.position
        return d


class TVSeries(BaseModel):
    url: HttpUrl
    name: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("url", mode="before")
    @classmethod
    def _check_allowed(cls, v: str):
        return _validate_allowed(v)


class SyncResult(BaseModel):
    source_url: HttpUrl
    tv_series: TVSeries
    new_episodes: List[Episode]

    @field_validator("source_url", mode="before")
    @classmethod
    def _check_allowed(cls, v: str):
        return _validate_allowed(v)

    def to_payload(self) -> Dict[str, Any]:
        """
        Exact structure previously emitted on stdout and used by Discord.
        """
        return {
            "source_url": str(self.source_url),
            "tv_series": self.tv_series.metadata
            or {"name": self.tv_series.name or str(self.source_url)},
            "new_episodes": [e.model_dump_public() for e in self.new_episodes],
        }
