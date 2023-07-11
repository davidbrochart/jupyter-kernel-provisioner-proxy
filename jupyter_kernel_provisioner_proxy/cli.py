import json
import sys
from pathlib import Path
from uuid import uuid4

import rich_click as click


@click.group()
def main():
    pass


@main.command()
@click.option("--display", default="", help="The kernel's display name")
@click.option("--language", default="python", help="The kernel's language name")
@click.option("--kernel", default="python3", help="The kernel's name")
@click.option("--url", default="http://localhost:8000", help="The kernel server URL")
def install(display: str, language: str, kernel: str, url: str):
    if not display:
        display = f"Remote {kernel} at {url}"
    kernelspec = {
        "argv": [],
        "display_name": display,
        "language": language,
        "interrupt_mode": "signal",
        "metadata": {
            "kernel_provisioner": {
                "provisioner_name": "jupyter-kernel-provisioner-proxy",
                "config": {
                    "kernel_url": url,
                    "kernel_name": kernel,
                }
            }
        }
    }
    directory_name = f"{language}-{uuid4().hex}"
    directory = Path(sys.prefix) / "share" / "jupyter" / "kernels" / directory_name
    directory.mkdir(parents=True, exist_ok=False)
    file = directory / "kernel.json"
    file.write_text(json.dumps(kernelspec, indent=2))
