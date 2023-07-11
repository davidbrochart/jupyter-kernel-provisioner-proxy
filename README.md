# jupyter-kernel-provisioner-proxy

A Jupyter kernel [provisioner](https://jupyter-client.readthedocs.io/en/stable/provisioning.html) that actually talks to a remote kernel through the kernel REST API of a Jupyter server.

## Usage

### Main server side

Install jupyter-kernel-provisioner-proxy:

```bash
pip install jupyter-kernel-provisioner-proxy
```

Then install a kernelspec with the `jupyter-kernel-provisioner-proxy install` command.

```bash
$ jupyter-kernel-provisioner-proxy install --help

 Usage: jupyter-kernel-provisioner-proxy install [OPTIONS]

╭─ Options ────────────────────────────────────────────╮
│ --display     TEXT  The kernel's display name        │
│ --language    TEXT  The kernel's language name       │
│ --kernel      TEXT  The kernel's name                │
│ --url         TEXT  The kernel server URL            │
│ --help              Show this message and exit.      │
╰──────────────────────────────────────────────────────╯
```

By default, `jupyter-kernel-provisioner-proxy install` will install a Python kernelspec, with the Jupyter server serving remote kernels at http://localhost:8000.

### Kernel server side

You can use [jupyter-server](https://github.com/jupyter-server/jupyter_server) or [jupyverse](https://github.com/jupyter-server/jupyverse) to serve remote kernels.

#### Using jupyter-server

```bash
pip install jupyter-server
jupyter server --ServerApp.token='' --ServerApp.password='' --ServerApp.disable_check_xsrf=True --no-browser --port=8000
```

#### Using jupyverse

```bash
pip install jupyverse[jupyterlab,noauth]
jupyverse
```
