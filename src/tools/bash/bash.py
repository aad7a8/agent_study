import os
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tools._security import safe_path


def bash(command: str, timeout: int = 120, workdir: str = None) -> dict:
    safe_workdir = None
    if workdir is not None:
        try:
            safe_workdir = safe_path(workdir)
        except ValueError as e:
            return {"stdout": "", "stderr": f"Security error: {e}", "returncode": -1}

        if not os.path.isdir(safe_workdir):
            return {
                "stdout": "",
                "stderr": f"workdir does not exist or is not a directory: {workdir}",
                "returncode": -1,
            }

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=safe_workdir,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "returncode": -1,
        }
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1}
