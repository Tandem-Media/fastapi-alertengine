# ⚡ fastapi-alertengine

[![PyPI version](https://badge.fury.io/py/fastapi-alertengine.svg)](https://pypi.org/project/fastapi-alertengine/)
[![Tests](https://img.shields.io/badge/tests-259%20passed-brightgreen)](https://github.com/Tandem-Media/fastapi-alertengine)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/fastapi-alertengine/)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/Tandem-Media/fastapi-alertengine/blob/main/LICENSE)
[![PyPI Downloads](https://img.shields.io/pypi/dm/fastapi-alertengine)](https://pypi.org/project/fastapi-alertengine/)


**Production-ready FastAPI monitoring in one line.**


No Prometheus. No Grafana. No dashboards required — but one is included.


---


🔥 **164/164 tests passing**
🏦 **Derived from financial-grade infrastructure (AnchorFlow / Tofamba)**
🤖 **AI-agent friendly (works with Claude / Copilot / Cursor)**
⚡ **Memory mode — runs without Redis at all**


---


## 🚀 Quickstart (one line)


```bash
pip install fastapi-alertengine
```


```python
from fastapi import FastAPI
from fastapi_alertengine import instrument


app = FastAPI()
instrument(app)   # set ALERTENGINE_REDIS_URL or run without Redis in memory mode
```


That’s it. Four endpoints are now live:


| Endpoint | Description |
