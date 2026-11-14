
"""Static contract checks (no web framework needed): every API call made by the frontend matches a backend route,
and every element id used by app.js exists."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def backend_routes():
    out = []
    for f in (ROOT / "backend" / "routers").glob("*.py"):
        t = f.read_text(encoding="utf-8")
        prefix = re.search(r'APIRouter\(prefix="([^"]*)"', t)
        for method, path in re.findall(r'@router\.(get|post|patch|put|delete)\("([^"]*)"', t):
            out.append((method.upper(), (prefix.group(1) if prefix else "") + path))
    return out


def _matches(call_path, route):
    rx = "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(route)) + "$"
    return bool(re.match(rx, call_path)) or (call_path.endswith("/") and re.match(rx, call_path + "x"))


def test_every_frontend_call_has_a_backend_route():
    routes = backend_routes()
    gets = [p for m, p in routes if m == "GET"]
    calls = re.findall(r'\bapi\(\s*"(/[^"]*)"', JS)
    assert calls, "no api() calls found"
    for c in calls:
        c = "/api" + c.split("?")[0]
        assert any(_matches(c, r) for r in gets), f"no GET route for {c}"
    for method, path in re.findall(r'\bsend\(\s*"(POST|PATCH|PUT)",\s*"(/[^"]*)"', JS):
        c = "/api" + path
        assert any(m == method and _matches(c, r) for m, r in routes), f"no {method} route for {c}"


def test_required_endpoints_exist():
    have = set(backend_routes())
    for need in (("GET", "/api/kpi/plant"), ("GET", "/api/kpi/lines"), ("GET", "/api/machine-shifts"),
                 ("GET", "/api/machine-shifts/{aggregate_id}"), ("GET", "/api/alerts"), ("GET", "/api/warnings"),
                 ("POST", "/api/investigations"), ("GET", "/api/investigations"),
                 ("PATCH", "/api/investigations/{investigation_id}")):
        assert need in have, need


def test_element_ids_used_by_js_exist():
    static = set(re.findall(r'id="([^"]+)"', HTML))
    dynamic = set(re.findall(r'id="([^"$]+)"', JS))
    for used in set(re.findall(r'\$\("#([\w-]+)"\)', JS)):
        assert used in static | dynamic, f"#{used} not found in index.html or app.js templates"


def test_frontend_files_and_static_mount():
    for f in ("index.html", "app.js", "style.css"):
        assert (ROOT / "frontend" / f).exists()
    assert "app.js" in HTML and "style.css" in HTML
    assert 'app.mount("/"' in (ROOT / "backend" / "main.py").read_text(encoding="utf-8")


def test_js_escapes_untrusted_text():
    assert "const esc" in JS and "innerHTML" in JS
    assert not re.search(r"\$\{(?:a|i|w|l)\.(?:explanation|owner|title)\}", JS), "unescaped interpolation"
