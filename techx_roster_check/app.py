"""The local web page: pick an export, pick a rule pack, read the report.

Stateless by design. The browser sends the file's bytes as the body of one
request; they are checked in memory, the report goes back in the response,
and nothing is kept: no upload folder, no temp files, no database, no log of
contents. A plain HTML form upload was avoided on purpose, because the
multipart parser spools large uploads to a temporary file on disk.

There is no login, so the server only listens on 127.0.0.1 (enforced in
cli.py) and refuses requests that did not come from this machine's own
browser (the `local_only` middleware below).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import __version__
from .engine import check_file
from .packs import available_packs, find_pack, load_pack
from .reader import ReadError
from .report import esc, page, render_html
from .rules import PackError

MAX_UPLOAD_BYTES = 50 * 1024 * 1024     # far above any real roster export
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Every response: never cache (reports hold student data), never sniff types,
# never send this page's address to another site.
PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _hostname(value: str | None) -> str:
    """The host part of a Host header or an origin, minus any port."""
    host = (value or "").strip()
    if host.startswith("["):                      # [::1]:8766
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if ":" in host else host


def _is_loopback_name(host: str) -> bool:
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_local_host_header(value: str | None) -> bool:
    return _is_loopback_name(_hostname(value))


def _is_local_origin(origin: str) -> bool:
    parsed = urlparse(origin)
    return parsed.scheme in ("http", "https") and _is_loopback_name(_hostname(parsed.netloc))


def create_app() -> FastAPI:
    app = FastAPI(title="TechX Roster Check", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        """Answer only this machine's own browser.

        Listening on 127.0.0.1 stops other computers connecting, but not a web
        page the user happens to visit. Two tricks remain:

        * DNS rebinding: a site whose name re-resolves to 127.0.0.1 becomes
          "same-origin" with this app. The browser still sends that site's
          name in the Host header, so checking Host defeats it.
        * Cross-site requests: a foreign page can POST to /check. Refusing a
          foreign Origin header on anything but a read closes that.
        """
        if not _is_local_host_header(request.headers.get("host")):
            return JSONResponse({"detail": "requests must address this service as localhost"},
                                status_code=421, headers=PRIVATE_HEADERS)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and not _is_local_origin(origin):
                return JSONResponse({"detail": "cross-origin requests are not accepted"},
                                    status_code=403, headers=PRIVATE_HEADERS)
        response = await call_next(request)
        response.headers.update(PRIVATE_HEADERS)
        return response

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(upload_page())

    @app.post("/check")
    async def check(request: Request, pack: str, name: str = "export"):
        """Body: the export's raw bytes. Returns the report page."""
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
            return _too_large()
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_UPLOAD_BYTES:
                return _too_large()
        if not bytes(data).strip():
            return _problem("The file is empty.")
        try:
            # Packs are read fresh on every check, so an edit to a pack file
            # (or its school list) takes effect without restarting anything.
            # Only pack *names* are accepted here, never file paths.
            rule_pack = load_pack(find_pack(pack, allow_paths=False))
            result = await run_in_threadpool(check_file, bytes(data), name[:200], rule_pack)
        except (PackError, ReadError) as exc:
            return _problem(str(exc))
        finally:
            data.clear()
        return HTMLResponse(render_html(result, back_url=str(request.base_url)))

    return app


def _problem(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


def _too_large() -> JSONResponse:
    limit = MAX_UPLOAD_BYTES // (1024 * 1024)
    return _problem(f"The file is larger than {limit} MB, which is more than any roster "
                    "export should be. Check that the right file was chosen.", status=413)


# ------------------------------------------------------------ upload page
UPLOAD_SCRIPT = """
(function () {
  var form = document.getElementById("check-form");
  var input = document.getElementById("file");
  var drop = document.getElementById("drop");
  var dropText = document.getElementById("drop-text");
  var packSelect = document.getElementById("pack");
  var packDesc = document.getElementById("pack-desc");
  var button = document.getElementById("go");
  var msg = document.getElementById("msg");

  function showPack() {
    var option = packSelect.options[packSelect.selectedIndex];
    packDesc.textContent = option ? option.dataset.desc : "";
  }
  function showFile() {
    var file = input.files[0];
    drop.classList.toggle("chosen", !!file);
    dropText.textContent = file ? file.name : dropText.dataset.empty;
  }
  function say(text, isError) {
    msg.textContent = text;
    msg.classList.toggle("error", !!isError);
  }

  packSelect.addEventListener("change", showPack);
  input.addEventListener("change", showFile);
  ["dragenter", "dragover"].forEach(function (type) {
    drop.addEventListener(type, function () { drop.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (type) {
    drop.addEventListener(type, function () { drop.classList.remove("over"); });
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var file = input.files[0];
    if (!file) { say("Choose an export file first.", true); return; }
    button.disabled = true;
    say("Checking " + file.name + "...");
    var url = "check?pack=" + encodeURIComponent(packSelect.value) +
              "&name=" + encodeURIComponent(file.name);
    fetch(url, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file })
      .then(function (res) {
        if (res.ok) {
          return res.text().then(function (html) {
            // Show the report as a page of its own. The browser's Back
            // button returns here.
            var blob = new Blob([html], { type: "text/html" });
            window.location.assign(URL.createObjectURL(blob));
          });
        }
        return res.json().then(function (body) { say(body.detail || "The check failed.", true); });
      })
      .catch(function () { say("Could not reach Roster Check. Is it still running?", true); })
      .then(function () { button.disabled = false; });
  });

  showPack();
  showFile();
})();
"""


def upload_page() -> str:
    options = []
    for name, path in available_packs().items():
        try:
            pack = load_pack(path)
            title, desc, disabled = pack.name, pack.description, ""
        except PackError as exc:
            # A broken pack still appears, with its mistake, so the person
            # editing it can see what to fix.
            title, desc, disabled = f"{name} (has a mistake)", str(exc), " disabled"
        selected = " selected" if name == "health" and not disabled else ""
        options.append(f'<option value="{esc(name)}" data-desc="{esc(desc)}"{selected}{disabled}>'
                       f"{esc(title)}</option>")

    body = f"""<header class="topbar">
  <div class="brand">TechX Roster Check</div>
  <div class="meta">local only · nothing is saved · {esc(__version__)}</div>
</header>
<main class="narrow">
  <section class="panel pad">
    <h1>Check a PowerSchool export</h1>
    <p class="intro">Choose a file from Data Export Manager (tab-delimited or CSV). It is read in
    memory on this computer, checked, and forgotten as soon as the report appears.</p>
    <form id="check-form">
      <label class="drop" id="drop">
        <input type="file" id="file" accept=".csv,.txt,.tsv,.text,text/csv,text/plain,text/tab-separated-values">
        <span id="drop-text" data-empty="Drop the export here, or click to choose it"></span>
      </label>
      <label class="field" for="pack">Rule pack</label>
      <select id="pack" class="field-select">{"".join(options)}</select>
      <p class="pack-desc" id="pack-desc"></p>
      <button class="btn primary" type="submit" id="go">Check file</button>
      <p class="msg" id="msg" role="status"></p>
    </form>
  </section>
</main>"""
    return page("TechX Roster Check", body, f"<script>{UPLOAD_SCRIPT}</script>")
