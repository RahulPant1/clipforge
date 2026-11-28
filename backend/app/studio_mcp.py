"""Optional local stdio adapter for the running ClipForge API."""
import os
import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from requests import RequestException, Session

server = FastMCP("ClipForge Studio")


def _request(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    port = int(os.getenv("CLIPFORGE_MCP_PORT", "8000"))
    with Session() as session:
        session.trust_env = False
        try:
            response = session.request(method, f"http://127.0.0.1:{port}/api/script-shorts{path}",
                                       json=body, timeout=10, allow_redirects=False)
        except RequestException as error:
            raise ValueError("Start the local ClipForge API before calling Studio tools.") from error
    if not response.ok or response.is_redirect:
        raise ValueError(f"ClipForge API returned HTTP {response.status_code}: {response.text[:400]}")
    return response.json()


def _job_path(job_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{10}", job_id):
        raise ValueError("Use the job ID returned by create_studio_draft.")
    return f"/studio-jobs/{job_id}"


@server.tool()
def create_studio_draft(brief: str, source_notes: str = "") -> dict[str, Any]:
    """Queue a local Studio draft with a script and storyboard, and return its job ID."""
    return _request("POST", "/studio-jobs", {"brief": brief, "source_notes": source_notes})


@server.tool()
def studio_job_status(job_id: str) -> dict[str, Any]:
    """Check whether a Studio draft is queued, running, ready, or failed."""
    return _request("GET", _job_path(job_id))


@server.tool()
def list_studio_outputs(job_id: str) -> dict[str, Any]:
    """List the local draft path and any exports made for that draft in the Studio UI."""
    return _request("GET", f"{_job_path(job_id)}/outputs")


if __name__ == "__main__":
    server.run(transport="stdio")
